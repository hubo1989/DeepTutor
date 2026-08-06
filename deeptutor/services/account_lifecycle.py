"""Account data export and recoverable, idempotent local-account erasure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
from typing import Any, Iterable
import zipfile

from deeptutor.multi_user import grants, identity, paths
from deeptutor.services.private_state import (
    chmod_private,
    ensure_private_directory,
    ensure_private_file,
    exclusive_path_lock,
    harden_sqlite_files,
)

_lifecycle_lock = threading.RLock()
_QUOTA_TABLES = (
    "token_usage",
    "token_reservations",
    "resource_usage",
    "resource_reservations",
)
_BYOK_USAGE_TABLES = ("byok_usage",)
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
logger = logging.getLogger(__name__)


class AccountLifecycleError(RuntimeError):
    """Base class for account export/deletion failures."""


class AccountNotFound(AccountLifecycleError):
    """No local account exists for the requested username."""


class AccountLifecycleForbidden(AccountLifecycleError):
    """The requested lifecycle operation is unsafe for this account."""


class AccountLifecycleConflict(AccountLifecycleForbidden):
    """The username now belongs to a different account incarnation."""


@dataclass(frozen=True, slots=True)
class AccountExport:
    path: Path
    file_count: int


@dataclass(frozen=True, slots=True)
class AccountDeletionResult:
    deleted: bool
    already_deleted: bool
    user_id: str = ""


@dataclass(frozen=True, slots=True)
class AccountDeletionContext:
    """Durable identity binding carried across a staged deletion saga."""

    username: str
    user_id: str
    request_key: str
    already_deleted: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_username(username: str) -> str:
    value = str(username or "").strip()
    return value.casefold() if "@" in value else value


def _auth_secret() -> bytes:
    from deeptutor.services import auth as auth_service

    secret = str(getattr(auth_service, "AUTH_SECRET", "") or "")
    if not secret:
        secret = identity.load_or_create_auth_secret()
    return secret.encode("utf-8")


def _deletion_key(username: str) -> str:
    return hmac.new(
        _auth_secret(),
        f"account-deletion\x00{_canonical_username(username)}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _validated_user_id(value: str) -> str:
    user_id = str(value or "").strip()
    if not _USER_ID_RE.fullmatch(user_id):
        raise AccountLifecycleForbidden("Account has an invalid storage identity")
    return user_id


def _db_path() -> Path:
    return identity.AUTH_DIR / "account_lifecycle.sqlite3"


def _connect() -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        path = _db_path()
        ensure_private_file(path)
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA secure_delete = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS account_deletions (
                request_key TEXT PRIMARY KEY,
                user_id TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                actor_id TEXT NOT NULL DEFAULT '',
                actor_username TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS account_exports (
                export_id TEXT PRIMARY KEY,
                target_user_id TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                file_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        harden_sqlite_files(path)
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise AccountLifecycleError("Account lifecycle ledger is unavailable") from exc


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _usage_rows(db_path: Path, tables: Iterable[str], user_id: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    if not db_path.exists():
        return result
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        for table in tables:
            if not _table_exists(connection, table):
                continue
            result[table] = [
                dict(row)
                for row in connection.execute(
                    f'SELECT * FROM "{table}" WHERE user_id = ?', (user_id,)
                ).fetchall()
            ]
    finally:
        connection.close()
    return result


def _quota_db_path() -> Path:
    from deeptutor.multi_user.token_quota import quota_db_path

    return quota_db_path()


def _byok_usage_db_path() -> Path:
    from deeptutor.multi_user.byok_usage import _db_path as byok_usage_db_path

    return byok_usage_db_path()


def _safe_workspace_files(root: Path):
    if not root.is_dir() or root.is_symlink():
        return
    resolved_root = root.resolve()
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(resolved_root)
        except (FileNotFoundError, ValueError):
            continue
        yield resolved, relative


def _portable_user_files(user_root: Path):
    """Yield only business/user-authored data from a regular-user root.

    ``user/private`` contains OAuth credentials and ``user/settings`` may
    contain bearer tokens or deployment configuration, so this is an explicit
    allowlist rather than a root traversal with an ever-growing denylist.
    """
    allowed_directories = (
        Path("workspace"),  # legacy per-user layout still used by older installs
        Path("user/workspace"),
        Path("knowledge_bases"),
        Path("memory"),
    )
    for relative_root in allowed_directories:
        source_root = user_root / relative_root
        for source, relative in _safe_workspace_files(source_root) or ():
            yield source, Path("workspace") / relative_root / relative

    chat_db = user_root / "user" / "chat_history.db"
    if chat_db.is_file() and not chat_db.is_symlink():
        try:
            resolved = chat_db.resolve(strict=True)
            resolved.relative_to(user_root.resolve())
        except (FileNotFoundError, ValueError):
            return
        yield resolved, Path("workspace/user/chat_history.db")


def _public_profile(username: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "username": username,
        "role": str(record.get("role") or "user"),
        "created_at": str(record.get("created_at") or ""),
        "disabled": bool(record.get("disabled", False)),
        "avatar": str(record.get("avatar") or ""),
        "email_verified": bool(record.get("email_verified", True)),
    }


def _portable_cron_jobs(user_id: str) -> list[dict[str, Any]]:
    from deeptutor.services.cron import get_cron_service

    return [asdict(job) for job in get_cron_service().list_jobs(owner_key=f"chat:{user_id}")]


def _external_attachment_root(user_id: str) -> Path | None:
    from deeptutor.services.storage.attachment_store import (
        external_attachment_root_for_owner,
    )

    return external_attachment_root_for_owner(user_id)


def _remove_user_cron_jobs(user_id: str) -> None:
    from deeptutor.services.cron import get_cron_service

    get_cron_service().remove_owner_jobs(f"chat:{user_id}")


def remove_scheduled_account_jobs(user_id: str) -> None:
    """Quiesce scheduled work before the account deletion commit point."""

    _remove_user_cron_jobs(_validated_user_id(user_id))


def export_account(
    username: str,
    *,
    actor_id: str = "",
    commercial_data: Any | None = None,
) -> AccountExport:
    """Create a temporary ZIP without password hashes or BYOK secrets."""
    canonical = _canonical_username(username)
    with _lifecycle_lock:
        record = identity.get_user(canonical)
        if record is None:
            raise AccountNotFound("User not found")
        if bool(record.get("disabled", False)):
            raise AccountLifecycleForbidden("Disabled accounts cannot start a new export")
        user_id = _validated_user_id(str(record.get("id") or ""))
        role = str(record.get("role") or "user")

        fd, temp_name = tempfile.mkstemp(prefix="deeptutor-account-", suffix=".zip")
        os.close(fd)
        archive_path = Path(temp_name)
        file_count = 0
        try:
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                archive.writestr(
                    "account/profile.json",
                    _json_bytes(_public_profile(canonical, record)),
                )
                file_count += 1
                if role != "admin":
                    grant = grants.load_grant(user_id)
                    archive.writestr("account/grant.json", _json_bytes(grant))
                    file_count += 1

                byok_root = paths.SYSTEM_ROOT / "byok" / user_id
                byok_export: dict[str, Any] = {"profiles": [], "preferences": {}}
                if byok_root.is_dir() and not byok_root.is_symlink():
                    from deeptutor.multi_user.byok_vault import get_user_byok_vault

                    vault = get_user_byok_vault()
                    byok_export = {
                        "profiles": vault.list_profiles(user_id),
                        "preferences": vault.get_preferences(user_id),
                    }
                archive.writestr("account/byok.json", _json_bytes(byok_export))
                file_count += 1

                usage = {
                    "platform": _usage_rows(_quota_db_path(), _QUOTA_TABLES, user_id),
                    "byok": _usage_rows(_byok_usage_db_path(), _BYOK_USAGE_TABLES, user_id),
                }
                archive.writestr("account/usage.json", _json_bytes(usage))
                file_count += 1

                archive.writestr(
                    "account/cron_jobs.json",
                    _json_bytes(_portable_cron_jobs(user_id)),
                )
                file_count += 1

                if commercial_data is not None:
                    # Commercial callers supply an already privacy-filtered
                    # snapshot; the archive path is fixed and cannot widen the
                    # local filesystem allowlist.
                    archive.writestr(
                        "account/commercial.json",
                        _json_bytes(commercial_data),
                    )
                    file_count += 1

                # Admin workspaces are deployment-global and can contain other
                # accounts/system secrets, so only regular-user roots are portable.
                if role != "admin":
                    workspace = paths.USERS_ROOT / user_id
                    for source, archive_name in _portable_user_files(workspace) or ():
                        archive.write(source, archive_name.as_posix())
                        file_count += 1
                    external_attachments = _external_attachment_root(user_id)
                    if external_attachments is not None:
                        for source, relative in _safe_workspace_files(external_attachments) or ():
                            archive.write(
                                source,
                                (
                                    Path("workspace/user/workspace/chat/attachments") / relative
                                ).as_posix(),
                            )
                            file_count += 1

            export_id = hashlib.sha256(
                f"{user_id}\x00{_now()}\x00{secrets.token_hex(16)}".encode()
            ).hexdigest()
            connection = _connect()
            try:
                connection.execute(
                    "INSERT INTO account_exports "
                    "(export_id, target_user_id, actor_id, created_at, file_count) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (export_id, user_id, actor_id, _now(), file_count),
                )
            finally:
                connection.close()
            return AccountExport(path=archive_path, file_count=file_count)
        except AccountLifecycleError:
            archive_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            archive_path.unlink(missing_ok=True)
            raise AccountLifecycleError("Account export could not be completed") from exc


def _delete_sqlite_rows(db_path: Path, tables: Iterable[str], user_id: str) -> None:
    if not db_path.exists():
        return
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            if _table_exists(connection, table):
                connection.execute(f'DELETE FROM "{table}" WHERE user_id = ?', (user_id,))
        connection.execute("COMMIT")
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            # Row erasure is already committed; checkpointing is best-effort
            # and must not turn a completed cleanup step into a false failure.
            pass
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _remove_path(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=True)
    elif target.is_dir():
        shutil.rmtree(target)


def _scrub_jsonl(path: Path, *, user_id: str, username: str) -> None:
    if not path.is_file() or path.is_symlink():
        return
    with exclusive_path_lock(path):
        # Re-check after acquiring the lock because another process may have
        # atomically replaced or removed the log while we waited.
        if not path.is_file() or path.is_symlink():
            return
        kept: list[str] = []
        identity_fields = {
            "user_id",
            "target_user_id",
            "username",
            "actor_id",
            "actor_username",
        }
        identity_values = {user_id}
        if username:
            identity_values.add(username)
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                if user_id not in line and username not in line:
                    kept.append(line)
                continue
            if isinstance(payload, dict) and any(
                str(payload.get(field) or "") in identity_values for field in identity_fields
            ):
                continue
            kept.append(line)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                if kept:
                    handle.write("\n".join(kept) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            chmod_private(temp_path)
            os.replace(temp_path, path)
            chmod_private(path)
        finally:
            temp_path.unlink(missing_ok=True)


def _clear_registration_state(username: str) -> None:
    verification_db = identity.AUTH_DIR / "email_verification.sqlite3"
    if not verification_db.exists():
        return
    connection = sqlite3.connect(verification_db, timeout=10)
    try:
        connection.execute("PRAGMA secure_delete = ON")
        if _table_exists(connection, "pending_registrations"):
            connection.execute("DELETE FROM pending_registrations WHERE email = ?", (username,))
        if _table_exists(connection, "verification_rate_limits"):
            connection.execute(
                "DELETE FROM verification_rate_limits WHERE subject = ?",
                (f"email:{username}",),
            )
        connection.commit()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    finally:
        connection.close()


def _deletion_fence_path(user_id: str) -> Path:
    return identity.AUTH_DIR / "deletion_fences" / _validated_user_id(user_id)


def _install_deletion_fence(user_id: str) -> None:
    fence = _deletion_fence_path(user_id)
    ensure_private_directory(fence.parent)
    ensure_private_file(fence)


def is_account_deletion_fenced(user_id: str) -> bool:
    """Return whether new work must be rejected for this immutable owner id."""
    try:
        fence = _deletion_fence_path(user_id)
    except AccountLifecycleForbidden:
        return False
    return fence.is_file() and not fence.is_symlink()


def _clear_account_state(username: str, user_id: str) -> None:
    from deeptutor.services import login_rate_limit, password_reset

    grant_path = grants.GRANTS_DIR / f"{user_id}.json"
    _remove_path(grant_path)
    _remove_path(paths.SYSTEM_ROOT / "byok" / user_id)
    identity.delete_avatar_file(user_id)
    _delete_sqlite_rows(_quota_db_path(), _QUOTA_TABLES, user_id)
    _delete_sqlite_rows(_byok_usage_db_path(), _BYOK_USAGE_TABLES, user_id)
    if username:
        login_rate_limit.clear_login_history(username)
        password_reset.clear_account_state(username)
        _clear_registration_state(username)
    _scrub_jsonl(
        paths.SYSTEM_ROOT / "audit" / "usage.jsonl",
        user_id=user_id,
        username=username,
    )
    _scrub_jsonl(
        paths.SYSTEM_ROOT / "byok" / "audit.jsonl",
        user_id=user_id,
        username=username,
    )
    remove_scheduled_account_jobs(user_id)
    external_attachments = _external_attachment_root(user_id)
    if external_attachments is not None:
        _remove_path(external_attachments)
    _remove_path(paths.USERS_ROOT / user_id)
    for key in list(paths._path_services):
        if f":{user_id}:" in key:
            paths._path_services.pop(key, None)


def _mark_deletion_failed(request_key: str, exc: BaseException) -> None:
    try:
        connection = _connect()
        try:
            connection.execute(
                "UPDATE account_deletions SET status = 'failed', error_type = ? "
                "WHERE request_key = ? AND status <> 'completed'",
                (type(exc).__name__[:80], request_key),
            )
        finally:
            connection.close()
    except Exception:
        logger.exception("Could not persist account-deletion failure state")


def fail_account_deletion(
    context: AccountDeletionContext,
    exc: BaseException,
) -> None:
    """Persist failure for an external saga step without deleting identity."""
    canonical = _canonical_username(context.username)
    if context.request_key != _deletion_key(canonical):
        raise AccountLifecycleConflict("Invalid account deletion context")
    _validated_user_id(context.user_id)
    _mark_deletion_failed(context.request_key, exc)


def begin_account_deletion(
    username: str,
    *,
    actor_id: str = "",
    actor_username: str = "",
    expected_user_id: str | None = None,
) -> AccountDeletionContext:
    """Fence and disable one immutable account incarnation.

    The returned context is the only value accepted by
    :func:`finish_account_deletion`.  Callers may safely await turn
    cancellation and commercial-data erasure between the two phases; if any
    intermediate step fails, the identity remains disabled and recoverable.
    """
    canonical = _canonical_username(username)
    if not canonical:
        raise AccountNotFound("User not found")
    requested_id = _validated_user_id(expected_user_id) if expected_user_id else None
    request_key = _deletion_key(canonical)

    with _lifecycle_lock:
        connection = _connect()
        try:
            prior = connection.execute(
                "SELECT user_id, status FROM account_deletions WHERE request_key = ?",
                (request_key,),
            ).fetchone()
            record = identity.get_user(canonical)
            prior_status = str(prior["status"] or "") if prior is not None else ""
            prior_id = str(prior["user_id"] or "") if prior is not None else ""
            if prior_id:
                prior_id = _validated_user_id(prior_id)

            if prior_status == "completed" and record is None:
                if requested_id and prior_id and requested_id != prior_id:
                    raise AccountLifecycleConflict(
                        "Account deletion belongs to a different user incarnation"
                    )
                return AccountDeletionContext(
                    canonical,
                    prior_id or requested_id or "",
                    request_key,
                    already_deleted=True,
                )

            current_id = ""
            if record is not None:
                current_id = _validated_user_id(str(record.get("id") or ""))

            unfinished_prior = bool(prior_id and prior_status in {"in_progress", "failed"})
            if unfinished_prior:
                user_id = prior_id
                if requested_id and requested_id != user_id:
                    raise AccountLifecycleConflict(
                        "Account deletion belongs to a different user incarnation"
                    )
                if current_id and current_id != user_id and requested_id != user_id:
                    # An unbound retry must never adopt a newly registered
                    # account that reused the username.
                    raise AccountLifecycleConflict(
                        "Username now belongs to a different user incarnation"
                    )
            else:
                if record is None:
                    raise AccountNotFound("User not found")
                user_id = current_id
                if requested_id and requested_id != user_id:
                    raise AccountLifecycleConflict(
                        "Username now belongs to a different user incarnation"
                    )

            same_identity = record is not None and current_id == user_id
            if same_identity and str(record.get("role") or "user") == "admin":
                raise AccountLifecycleForbidden(
                    "Demote the administrator before deleting the account"
                )

            safe_actor_username = actor_username if actor_id != user_id else "self"
            connection.execute(
                """
                INSERT INTO account_deletions
                    (request_key, user_id, status, started_at, actor_id, actor_username)
                VALUES (?, ?, 'in_progress', ?, ?, ?)
                ON CONFLICT(request_key) DO UPDATE SET
                    user_id = excluded.user_id,
                    status = 'in_progress',
                    started_at = excluded.started_at,
                    completed_at = NULL,
                    actor_id = excluded.actor_id,
                    actor_username = excluded.actor_username,
                    error_type = ''
                """,
                (request_key, user_id, _now(), actor_id, safe_actor_username),
            )
        finally:
            connection.close()

        try:
            _install_deletion_fence(user_id)
            if same_identity and not identity.set_disabled(
                canonical,
                True,
                expected_user_id=user_id,
            ):
                raise AccountLifecycleConflict(
                    "Username changed while account deletion was starting"
                )
        except Exception as exc:
            _mark_deletion_failed(request_key, exc)
            if isinstance(exc, AccountLifecycleForbidden):
                raise
            raise AccountLifecycleError(
                "Account deletion could not be fenced; the account may be disabled"
            ) from exc

        return AccountDeletionContext(canonical, user_id, request_key)


def finish_account_deletion(context: AccountDeletionContext) -> AccountDeletionResult:
    """Erase local state and commit identity deletion for a fenced context."""
    if context.already_deleted:
        return AccountDeletionResult(True, True, context.user_id)
    canonical = _canonical_username(context.username)
    user_id = _validated_user_id(context.user_id)
    if context.request_key != _deletion_key(canonical):
        raise AccountLifecycleConflict("Invalid account deletion context")

    with _lifecycle_lock:
        try:
            connection = _connect()
            try:
                prior = connection.execute(
                    "SELECT user_id, status FROM account_deletions WHERE request_key = ?",
                    (context.request_key,),
                ).fetchone()
            finally:
                connection.close()
            if prior is None or str(prior["user_id"] or "") != user_id:
                raise AccountLifecycleConflict(
                    "Account deletion context no longer matches its ledger"
                )
            if str(prior["status"] or "") == "completed":
                return AccountDeletionResult(True, True, user_id)

            _install_deletion_fence(user_id)
            record = identity.get_user(canonical)
            current_id = (
                _validated_user_id(str(record.get("id") or "")) if record is not None else ""
            )
            same_identity = record is not None and current_id == user_id
            if same_identity:
                if str(record.get("role") or "user") == "admin":
                    raise AccountLifecycleForbidden(
                        "Demote the administrator before deleting the account"
                    )
                if not identity.set_disabled(
                    canonical,
                    True,
                    expected_user_id=user_id,
                ):
                    raise AccountLifecycleConflict(
                        "Username changed while account deletion was finishing"
                    )

            # If a previous attempt deleted identity A and a new identity B
            # now owns the username, clean only A's immutable-id state.  Never
            # clear B's username-keyed reset/limiter records or delete B.
            cleanup_username = canonical if not current_id or same_identity else ""
            _clear_account_state(cleanup_username, user_id)

            if same_identity:
                removed = identity.delete_user(canonical, expected_user_id=user_id)
                remaining = identity.get_user(canonical)
                if (
                    not removed
                    and remaining is not None
                    and str(remaining.get("id") or "") == user_id
                ):
                    raise AccountLifecycleError("Account identity could not be deleted")

            connection = _connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM account_exports WHERE target_user_id = ?", (user_id,)
                )
                cursor = connection.execute(
                    "UPDATE account_deletions SET status = 'completed', "
                    "completed_at = ?, error_type = '' "
                    "WHERE request_key = ? AND user_id = ?",
                    (_now(), context.request_key, user_id),
                )
                if cursor.rowcount != 1:
                    raise AccountLifecycleConflict(
                        "Account deletion ledger changed before completion"
                    )
                connection.execute("COMMIT")
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
            return AccountDeletionResult(True, False, user_id)
        except Exception as exc:
            _mark_deletion_failed(context.request_key, exc)
            if isinstance(exc, AccountLifecycleForbidden):
                raise
            raise AccountLifecycleError(
                "Account deletion did not complete; the account remains disabled"
            ) from exc


def delete_account(
    username: str,
    *,
    actor_id: str = "",
    actor_username: str = "",
    expected_user_id: str | None = None,
) -> AccountDeletionResult:
    """Compatibility wrapper for local-only staged account erasure."""
    context = begin_account_deletion(
        username,
        actor_id=actor_id,
        actor_username=actor_username,
        expected_user_id=expected_user_id,
    )
    return finish_account_deletion(context)


__all__ = [
    "AccountDeletionContext",
    "AccountDeletionResult",
    "AccountExport",
    "AccountLifecycleConflict",
    "AccountLifecycleError",
    "AccountLifecycleForbidden",
    "AccountNotFound",
    "begin_account_deletion",
    "delete_account",
    "export_account",
    "fail_account_deletion",
    "finish_account_deletion",
    "is_account_deletion_fenced",
    "remove_scheduled_account_jobs",
]
