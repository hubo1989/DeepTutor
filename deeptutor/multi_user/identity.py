"""Canonical identity store for the optional multi-user layer."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import threading
from typing import Any
from uuid import uuid4

from deeptutor.services.private_state import (
    chmod_private,
    ensure_private_directory,
    ensure_private_file,
    exclusive_path_lock,
)

from .models import Role
from .paths import PROJECT_ROOT, SYSTEM_ROOT, migrate_legacy_multi_user_tree

logger = logging.getLogger(__name__)

# Serialises read-modify-write operations on USERS_FILE. This also makes the
# explicit no-existing-admin bootstrap check atomic for single-process FastAPI
# deployments. Multi-worker deployments need the shared control-plane store.
_USERS_WRITE_LOCK = threading.Lock()

AUTH_DIR = SYSTEM_ROOT / "auth"
USERS_FILE = AUTH_DIR / "users.json"
SECRET_FILE = AUTH_DIR / "auth_secret"
LEGACY_USERS_FILE = PROJECT_ROOT / "data" / "user" / "auth_users.json"
LEGACY_SECRET_FILE = PROJECT_ROOT / "data" / "user" / "auth_secret"


def new_user_id() -> str:
    return f"u_{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth_version(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _canonical_record(
    username: str,
    value: Any,
    *,
    default_role: Role = "user",
) -> dict[str, Any] | None:
    if isinstance(value, str):
        return {
            "id": new_user_id(),
            "hash": value,
            "role": default_role,
            "created_at": utc_now(),
            "disabled": False,
            "avatar": "",
            "auth_version": 0,
            # Existing records are trusted legacy accounts. New public
            # registrations are only written after email verification.
            "email_verified": True,
        }
    if not isinstance(value, dict):
        return None
    hashed = str(value.get("hash") or value.get("password_hash") or "")
    if not hashed:
        return None
    role = str(value.get("role") or default_role)
    if role not in {"admin", "user"}:
        role = default_role
    return {
        "id": str(value.get("id") or new_user_id()),
        "hash": hashed,
        "role": role,
        "created_at": str(value.get("created_at") or utc_now()),
        "disabled": bool(value.get("disabled", False)),
        "avatar": str(value.get("avatar") or ""),
        "email_verified": bool(value.get("email_verified", True)),
        "auth_version": _auth_version(value.get("auth_version")),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}


def _write_users(users: dict[str, dict[str, Any]]) -> None:
    ensure_private_directory(USERS_FILE.parent)
    # A truncated users.json can lock every account out after a crash. Write
    # and fsync a sibling temp file, then atomically replace the live store.
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{USERS_FILE.name}.", suffix=".tmp", dir=USERS_FILE.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(users, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, USERS_FILE)
        chmod_private(USERS_FILE)
    finally:
        temp_path.unlink(missing_ok=True)


def _migrate_legacy_users() -> dict[str, dict[str, Any]] | None:
    if USERS_FILE.exists() or not LEGACY_USERS_FILE.exists():
        return None
    legacy = _read_json(LEGACY_USERS_FILE)
    users: dict[str, dict[str, Any]] = {}
    for username, value in legacy.items():
        role: Role = "admin" if not users else "user"
        if isinstance(value, dict) and str(value.get("role") or "") in {"admin", "user"}:
            role = str(value.get("role"))  # type: ignore[assignment]
        record = _canonical_record(username, value, default_role=role)
        if record is not None:
            users[str(username)] = record
    if users:
        _write_users(users)
        logger.info("Migrated auth users from %s to %s", LEGACY_USERS_FILE, USERS_FILE)
        return users
    return None


def _migrate_secret() -> None:
    if SECRET_FILE.exists() or not LEGACY_SECRET_FILE.exists():
        return
    try:
        secret = LEGACY_SECRET_FILE.read_text(encoding="utf-8").strip()
        if secret:
            ensure_private_file(SECRET_FILE)
            SECRET_FILE.write_text(secret, encoding="utf-8")
            chmod_private(SECRET_FILE)
            logger.info("Migrated auth secret from %s to %s", LEGACY_SECRET_FILE, SECRET_FILE)
    except Exception as exc:
        logger.warning("Failed to migrate legacy auth secret: %s", exc)


def _env_bootstrap_admin() -> tuple[str, str]:
    """Return ``(username, password_hash)`` for the ``auth.json`` bootstrap admin.

    Both halves are required: the shipped default seeds a username with an
    empty hash, which cannot authenticate and therefore is not an admin. An
    empty tuple entry means "no bootstrap admin configured".

    :mod:`deeptutor.services.auth` is imported lazily because it imports this
    module. Its resolved globals — rather than a fresh settings read — are the
    source of truth on purpose: they are exactly the credentials
    ``authenticate()`` accepts, so the promotion gate in :func:`save_user` and
    the login path can never disagree about whether an admin already exists.
    """
    try:
        from deeptutor.services import auth as auth_service

        username = str(getattr(auth_service, "AUTH_USERNAME", "") or "")
        password_hash = str(getattr(auth_service, "AUTH_PASSWORD_HASH", "") or "")
    except Exception as exc:  # pragma: no cover - auth settings unavailable
        logger.warning("Could not resolve the bootstrap admin credentials: %s", exc)
        return "", ""
    if not username or not password_hash:
        return "", ""
    return username, password_hash


def _env_admin_record(password_hash: str) -> dict[str, Any]:
    """Build the in-memory record representing the bootstrap admin."""
    return {
        "id": "env-admin",
        "hash": password_hash,
        "role": "admin",
        "created_at": "",
        "disabled": False,
        "avatar": "",
        "email_verified": True,
        "auth_version": 0,
    }


def load_users(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> dict[str, dict[str, Any]]:
    """Load canonical users, migrating legacy records and env fallback in memory."""
    migrate_legacy_multi_user_tree()
    ensure_private_directory(AUTH_DIR)
    chmod_private(USERS_FILE)
    users: dict[str, dict[str, Any]] | None = None
    if USERS_FILE.exists():
        users = _read_json(USERS_FILE)
    else:
        users = _migrate_legacy_users()

    if users is None:
        users = {}

    canonical: dict[str, dict[str, Any]] = {}
    changed = False
    for index, (username, value) in enumerate(users.items()):
        role: Role = "admin" if index == 0 else "user"
        if isinstance(value, dict) and str(value.get("role") or "") in {"admin", "user"}:
            role = str(value.get("role"))  # type: ignore[assignment]
        record = _canonical_record(str(username), value, default_role=role)
        if record is None:
            changed = True
            continue
        canonical[str(username)] = record
        changed = changed or record != value

    if USERS_FILE.exists() and changed:
        _write_users(canonical)

    # The bootstrap admin is merged in whenever it is configured, not only when
    # the store is empty. Falling back to it only for an empty store locked the
    # operator who bootstrapped the deployment out of their own instance the
    # moment the first real account was written (#849). A stored record with the
    # same username wins, so the account can later be adopted into the store.
    # The merge is deliberately in-memory only; no write path passes the env
    # arguments, so the bootstrap hash is never persisted into ``users.json``
    # where a rotation of ``auth.json`` could no longer supersede it.
    if env_username and env_password_hash and env_username not in canonical:
        merged = {env_username: _env_admin_record(env_password_hash)}
        merged.update(canonical)
        return merged

    return canonical


def save_user(username: str, hashed_password: str, role: Role = "user") -> dict[str, Any]:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Read-modify-write must be atomic so concurrent account writes are not lost.
    with _USERS_WRITE_LOCK:
        # Called without the env arguments on purpose: ``users`` is written back
        # to disk below, and the bootstrap admin must stay an in-memory overlay.
        users = load_users()
        # LearnLeader: admin promotion happens only in the email-verification
        # flow (first *verified* user) or via the admin panel — a plain save
        # keeps the caller's role, so public registration can never mint an
        # admin implicitly. Two exceptions keep operators in control: a stored
        # admin is never demoted by a re-save, and re-saving the configured
        # bootstrap admin's own username adopts that account into the store
        # with the admin role (the auth.json owner must not be able to lock
        # themselves out by registering their own username).
        env_username, _ = _env_bootstrap_admin()
        existing = users.get(username) or {}
        if str(existing.get("role") or "user") == "admin" or (
            env_username and env_username == username
        ):
            role = "admin"
        record = {
            "id": str(existing.get("id") or new_user_id()),
            "hash": hashed_password,
            "role": role,
            "created_at": str(existing.get("created_at") or utc_now()),
            "disabled": bool(existing.get("disabled", False)),
            "avatar": str(existing.get("avatar") or ""),
            "email_verified": True,
            "auth_version": _auth_version(existing.get("auth_version")),
        }
        users[username] = record
        _write_users(users)
    return record


def list_user_info(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> list[dict[str, Any]]:
    return [
        {
            "id": record.get("id", ""),
            "username": username,
            "role": record.get("role", "user"),
            "created_at": record.get("created_at", ""),
            "disabled": bool(record.get("disabled", False)),
            "avatar": str(record.get("avatar") or ""),
            "email_verified": bool(record.get("email_verified", True)),
        }
        for username, record in load_users(env_username, env_password_hash).items()
    ]


def get_user(username: str) -> dict[str, Any] | None:
    return load_users().get(username)


def get_user_by_id(user_id: str) -> tuple[str, dict[str, Any]] | None:
    for username, record in load_users().items():
        if str(record.get("id") or "") == user_id:
            return username, record
    return None


def delete_user(username: str, *, expected_user_id: str | None = None) -> bool:
    """Delete an account, optionally only if its incarnation still matches."""
    with _USERS_WRITE_LOCK:
        if not USERS_FILE.exists():
            return False
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        if expected_user_id is not None and str(record.get("id") or "") != expected_user_id:
            return False
        users.pop(username, None)
        _write_users(users)
        return True


def set_disabled(
    username: str,
    disabled: bool,
    *,
    expected_user_id: str | None = None,
) -> bool:
    """Atomically enable or disable an existing local account."""
    if not USERS_FILE.exists():
        return False
    with _USERS_WRITE_LOCK:
        users = load_users()
        if username not in users:
            return False
        if (
            expected_user_id is not None
            and str(users[username].get("id") or "") != expected_user_id
        ):
            return False
        users[username]["disabled"] = bool(disabled)
        _write_users(users)
        return True


def update_password(username: str, hashed_password: str) -> bool:
    """Replace a password hash and revoke every previously issued JWT.

    ``auth_version`` is embedded in new tokens and compared during decode.
    Legacy accounts and tokens both normalize to version zero, preserving
    existing sessions until the first password reset.
    """
    if not hashed_password or not USERS_FILE.exists():
        return False
    with _USERS_WRITE_LOCK:
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        record["hash"] = hashed_password
        record["auth_version"] = _auth_version(record.get("auth_version")) + 1
        _write_users(users)
        return True


def set_avatar(username: str, avatar: str) -> bool:
    """Update the avatar marker for an existing user. Returns True on success."""
    if not USERS_FILE.exists():
        return False
    with _USERS_WRITE_LOCK:
        users = load_users()
        if username not in users:
            return False
        users[username]["avatar"] = avatar
        _write_users(users)
    return True


# ---------------------------------------------------------------------------
# Avatar image files — stored next to the user store, keyed by user id
# ---------------------------------------------------------------------------

# Extensions are derived from server-side content sniffing, never from the
# uploaded filename, so this list is also the full set of files we may serve.
AVATAR_EXTENSIONS = ("png", "jpg", "webp")


def _avatar_dir() -> Path:
    # Resolved lazily so tests that monkeypatch AUTH_DIR keep avatars isolated.
    return AUTH_DIR / "avatars"


def get_avatar_file(user_id: str) -> Path | None:
    """Return the stored avatar image for ``user_id``, or None."""
    for ext in AVATAR_EXTENSIONS:
        candidate = _avatar_dir() / f"{user_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def save_avatar_file(user_id: str, data: bytes, ext: str) -> Path:
    """Atomically persist an avatar image, replacing any previous one."""
    if ext not in AVATAR_EXTENSIONS:
        raise ValueError(f"Unsupported avatar extension: {ext!r}")
    directory = _avatar_dir()
    ensure_private_directory(directory)
    target = directory / f"{user_id}.{ext}"
    tmp = directory / f"{user_id}.{ext}.tmp"
    tmp.write_bytes(data)
    chmod_private(tmp)
    tmp.replace(target)
    chmod_private(target)
    # A re-upload may change the extension; drop stale siblings.
    for other in AVATAR_EXTENSIONS:
        if other != ext:
            (directory / f"{user_id}.{other}").unlink(missing_ok=True)
    return target


def delete_avatar_file(user_id: str) -> None:
    for ext in AVATAR_EXTENSIONS:
        (_avatar_dir() / f"{user_id}.{ext}").unlink(missing_ok=True)


def set_role(username: str, role: Role) -> bool:
    if role not in {"admin", "user"}:
        raise ValueError("role must be 'admin' or 'user'")
    if not USERS_FILE.exists():
        return False
    with _USERS_WRITE_LOCK:
        users = load_users()
        if username not in users:
            return False
        users[username]["role"] = role
        _write_users(users)
        return True


def create_user_if_absent(
    username: str,
    hashed_password: str,
    *,
    role: Role = "user",
    email_verified: bool = True,
    bootstrap_admin_username: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Create a user exactly once and return ``(created, record)``.

    The requested role is honored exactly. Deciding whether a verified email
    is the explicitly configured bootstrap administrator belongs to the auth
    service, not to the persistence layer.
    """
    with _USERS_WRITE_LOCK:
        users = load_users()
        existing = users.get(username)
        if existing is not None:
            return False, existing
        bootstrap = str(bootstrap_admin_username or "").strip().casefold()
        effective_role: Role = role
        has_admin = any(str(item.get("role") or "user") == "admin" for item in users.values())
        if not has_admin and bootstrap and username.strip().casefold() == bootstrap:
            effective_role = "admin"
        record = {
            "id": new_user_id(),
            "hash": hashed_password,
            "role": effective_role,
            "created_at": utc_now(),
            "disabled": False,
            "avatar": "",
            "email_verified": bool(email_verified),
            "auth_version": 0,
        }
        users[username] = record
        _write_users(users)
        return True, record


def load_or_create_auth_secret() -> str:
    migrate_legacy_multi_user_tree()
    try:
        with exclusive_path_lock(SECRET_FILE):
            _migrate_secret()
            if SECRET_FILE.exists():
                chmod_private(SECRET_FILE)
                existing = SECRET_FILE.read_text(encoding="utf-8").strip()
                if existing:
                    return existing
            ensure_private_file(SECRET_FILE)
            generated = secrets.token_hex(32)
            SECRET_FILE.write_text(generated, encoding="utf-8")
            chmod_private(SECRET_FILE)
            logger.warning(
                "Auth is enabled and no auth_secret file exists. "
                "Generated a stable local secret at %s.",
                SECRET_FILE,
            )
            return generated
    except Exception as exc:
        logger.warning("Failed to load/create auth secret at %s: %s", SECRET_FILE, exc)
        from deeptutor.commercial.runtime import commercial_mode_requested

        if commercial_mode_requested():
            raise RuntimeError(
                "Commercial mode cannot persist a stable authentication secret"
            ) from exc
        return secrets.token_hex(32)
