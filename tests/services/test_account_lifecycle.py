from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest


def _seed_usage(db_path: Path, user_id: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE token_usage (
                user_id TEXT, period TEXT, period_key TEXT,
                consumed_tokens INTEGER, reserved_tokens INTEGER, updated_at TEXT
            );
            CREATE TABLE token_reservations (
                reservation_id TEXT, user_id TEXT, daily_key TEXT, monthly_key TEXT,
                requested_tokens INTEGER, state TEXT, created_at TEXT, finalized_at TEXT
            );
            CREATE TABLE resource_usage (
                user_id TEXT, resource TEXT, unit TEXT, period TEXT, period_key TEXT,
                consumed_units INTEGER, reserved_units INTEGER, updated_at TEXT
            );
            CREATE TABLE resource_reservations (
                reservation_id TEXT, user_id TEXT, resource TEXT, unit TEXT,
                daily_key TEXT, monthly_key TEXT, requested_units INTEGER,
                state TEXT, created_at TEXT, finalized_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO token_usage VALUES (?, 'daily', '2026-08-05', 7, 0, 'now')",
            (user_id,),
        )


def _seed_byok_usage(db_path: Path, user_id: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE byok_usage (
                request_id TEXT, user_id TEXT, service TEXT, profile_id TEXT,
                provider TEXT, model TEXT, estimated_units INTEGER,
                reported_units INTEGER, status TEXT, created_at REAL, finalized_at REAL
            )
            """
        )
        connection.execute(
            "INSERT INTO byok_usage VALUES "
            "('r1', ?, 'llm', 'p1', 'openai', 'gpt', 10, 8, 'success', 1, 2)",
            (user_id,),
        )


def test_account_export_excludes_credentials_and_includes_workspace(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.multi_user import grants, identity, paths
    from deeptutor.services import account_lifecycle
    from deeptutor.services.auth import hash_password
    from deeptutor.services.cron import CronJob, CronOwner, CronSchedule
    from deeptutor.services.storage import attachment_store

    created, record = identity.create_user_if_absent(
        "user@example.com", hash_password("password1234"), role="user"
    )
    assert created is True
    user_id = record["id"]
    workspace = paths.USERS_ROOT / user_id
    (workspace / "user").mkdir(parents=True)
    (workspace / "user/chat_history.db").write_bytes(b"session-bytes")
    (workspace / "user/private/openai-codex").mkdir(parents=True)
    (workspace / "user/private/openai-codex/credentials.v1.json").write_text(
        '{"refresh_token":"codex-refresh-sentinel"}', encoding="utf-8"
    )
    (workspace / "user/settings").mkdir(parents=True)
    (workspace / "user/settings/skill_hub_auth.json").write_text(
        '{"token":"skill-hub-bearer-sentinel"}', encoding="utf-8"
    )
    (workspace / "workspace/chat").mkdir(parents=True)
    (workspace / "workspace/chat/note.txt").write_text("hello", encoding="utf-8")
    grants.GRANTS_DIR.mkdir(parents=True, exist_ok=True)
    (grants.GRANTS_DIR / f"{user_id}.json").write_text(
        json.dumps({"version": 3, "user_id": user_id}), encoding="utf-8"
    )
    monkeypatch.setenv("DEEPTUTOR_BYOK_MASTER_KEY", "account-export-test-key")
    from deeptutor.multi_user.byok_vault import get_user_byok_vault

    get_user_byok_vault().save_profile(
        user_id,
        {
            "service": "llm",
            "name": "My provider",
            "provider": "openai",
            "model": "gpt-test",
        },
        "super-secret-api-key",
    )
    external_base = auth_isolated_root / "external-attachments"
    monkeypatch.setattr(
        attachment_store,
        "load_system_settings",
        lambda: {"chat_attachment_dir": str(external_base)},
    )
    external_owner_root = attachment_store.external_attachment_root_for_owner(user_id)
    assert external_owner_root is not None
    (external_owner_root / "session-1").mkdir(parents=True)
    (external_owner_root / "session-1/a1_notes.txt").write_text(
        "external attachment", encoding="utf-8"
    )

    cron_job = CronJob(
        id="job-1",
        name="Review notes",
        message="Review chapter one",
        schedule=CronSchedule(kind="every", every_seconds=3600),
        owner=CronOwner(kind="chat", user_id=user_id, is_admin=False, session_id="s1"),
    )

    class ExportCronService:
        def list_jobs(self, *, owner_key: str):
            assert owner_key == f"chat:{user_id}"
            return [cron_job]

    monkeypatch.setattr(
        "deeptutor.services.cron.get_cron_service",
        lambda: ExportCronService(),
    )

    result = account_lifecycle.export_account(
        "user@example.com",
        commercial_data={"schema_version": 1, "subscriptions": []},
    )
    try:
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
            profile = json.loads(archive.read("account/profile.json"))
            assert "hash" not in profile
            assert profile["username"] == "user@example.com"
            assert "account/grant.json" in names
            assert "account/usage.json" in names
            assert "account/cron_jobs.json" in names
            assert "account/commercial.json" in names
            assert "workspace/user/chat_history.db" in names
            assert "workspace/workspace/chat/note.txt" in names
            assert "workspace/user/workspace/chat/attachments/session-1/a1_notes.txt" in names
            assert not any("user/private" in name for name in names)
            assert not any("user/settings" in name for name in names)
            assert b"password1234" not in result.path.read_bytes()
            assert b"super-secret-api-key" not in result.path.read_bytes()
            assert b"codex-refresh-sentinel" not in result.path.read_bytes()
            assert b"skill-hub-bearer-sentinel" not in result.path.read_bytes()
            byok = json.loads(archive.read("account/byok.json"))
            assert byok["profiles"][0]["configured"] is True
            assert "encrypted_secret" not in byok["profiles"][0]
            cron_jobs = json.loads(archive.read("account/cron_jobs.json"))
            assert cron_jobs[0]["owner"]["user_id"] == user_id
    finally:
        result.path.unlink(missing_ok=True)


def test_account_deletion_cleans_all_scoped_state_and_is_idempotent(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.multi_user import grants, identity, paths
    from deeptutor.services import account_lifecycle
    from deeptutor.services.auth import hash_password
    from deeptutor.services.storage import attachment_store

    token_db = paths.SYSTEM_ROOT / "usage.sqlite3"
    byok_usage_db = paths.SYSTEM_ROOT / "byok_usage.sqlite3"
    monkeypatch.setenv("DEEPTUTOR_USAGE_DB_PATH", str(token_db))
    monkeypatch.setenv("DEEPTUTOR_BYOK_USAGE_DB_PATH", str(byok_usage_db))
    created, record = identity.create_user_if_absent(
        "user@example.com", hash_password("password1234"), role="user"
    )
    assert created is True
    user_id = record["id"]

    workspace = paths.USERS_ROOT / user_id
    (workspace / "user/attachments").mkdir(parents=True)
    (workspace / "user/attachments/file.txt").write_text("private", encoding="utf-8")
    grant_path = grants.GRANTS_DIR / f"{user_id}.json"
    grant_path.parent.mkdir(parents=True, exist_ok=True)
    grant_path.write_text("{}", encoding="utf-8")
    byok_root = paths.SYSTEM_ROOT / "byok" / user_id
    byok_root.mkdir(parents=True)
    (byok_root / "profiles.v1.json").write_text('{"encrypted_secret":"secret"}')
    identity.save_avatar_file(user_id, b"\x89PNG\r\n\x1a\n", "png")
    _seed_usage(token_db, user_id)
    _seed_byok_usage(byok_usage_db, user_id)
    external_base = auth_isolated_root / "external-attachments"
    monkeypatch.setattr(
        attachment_store,
        "load_system_settings",
        lambda: {"chat_attachment_dir": str(external_base)},
    )
    external_owner_root = attachment_store.external_attachment_root_for_owner(user_id)
    assert external_owner_root is not None
    external_owner_root.mkdir(parents=True)
    (external_owner_root / "file.txt").write_text("private", encoding="utf-8")
    removed_cron_owners: list[str] = []

    class DeleteCronService:
        def remove_owner_jobs(self, owner_key: str) -> int:
            removed_cron_owners.append(owner_key)
            return 1

    monkeypatch.setattr(
        "deeptutor.services.cron.get_cron_service",
        lambda: DeleteCronService(),
    )

    first = account_lifecycle.delete_account(
        "user@example.com", actor_id="u_admin", actor_username="admin@example.com"
    )
    second = account_lifecycle.delete_account(
        "user@example.com", actor_id="u_admin", actor_username="admin@example.com"
    )

    assert first.deleted is True
    assert first.already_deleted is False
    assert second.deleted is True
    assert second.already_deleted is True
    assert identity.get_user("user@example.com") is None
    assert not workspace.exists()
    assert not grant_path.exists()
    assert not byok_root.exists()
    assert not external_owner_root.exists()
    assert removed_cron_owners == [f"chat:{user_id}"]
    assert identity.get_avatar_file(user_id) is None
    with sqlite3.connect(token_db) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM token_usage WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            == 0
        )
    with sqlite3.connect(byok_usage_db) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM byok_usage WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            == 0
        )

    audit_db = paths.SYSTEM_ROOT / "auth/account_lifecycle.sqlite3"
    raw_audit = audit_db.read_bytes()
    assert b"user@example.com" not in raw_audit
    with sqlite3.connect(audit_db) as connection:
        row = connection.execute("SELECT status, completed_at FROM account_deletions").fetchone()
    assert row[0] == "completed"
    assert row[1]


def test_failed_deletion_leaves_disabled_identity_and_retry_completes(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import account_lifecycle

    created, record = identity.create_user_if_absent("retry@example.com", "hash", role="user")
    assert created is True
    original_cleanup = account_lifecycle._clear_account_state
    attempts = 0

    def flaky_cleanup(username: str, user_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated cleanup interruption")
        original_cleanup(username, user_id)

    monkeypatch.setattr(account_lifecycle, "_clear_account_state", flaky_cleanup)
    with pytest.raises(account_lifecycle.AccountLifecycleError):
        account_lifecycle.delete_account("retry@example.com", actor_id="u_admin")

    pending = identity.get_user("retry@example.com")
    assert pending is not None
    assert pending["id"] == record["id"]
    assert pending["disabled"] is True

    retried = account_lifecycle.delete_account("retry@example.com", actor_id="u_admin")
    assert retried.deleted is True
    assert retried.already_deleted is False
    assert identity.get_user("retry@example.com") is None


def test_reregistered_username_gets_a_new_deletion_lifecycle(
    auth_isolated_root,
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import account_lifecycle

    _, first = identity.create_user_if_absent("again@example.com", "hash", role="user")
    account_lifecycle.delete_account("again@example.com", actor_id="u_admin")
    _, second = identity.create_user_if_absent("again@example.com", "hash", role="user")
    assert second["id"] != first["id"]

    result = account_lifecycle.delete_account("again@example.com", actor_id="u_admin")

    assert result.deleted is True
    assert result.already_deleted is False
    assert identity.get_user("again@example.com") is None


def test_invalid_user_id_cannot_escape_scoped_deletion_roots(
    auth_isolated_root,
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import account_lifecycle

    identity.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    identity.USERS_FILE.write_text(
        json.dumps(
            {
                "unsafe@example.com": {
                    "id": "../../outside",
                    "hash": "hash",
                    "role": "user",
                    "created_at": "now",
                }
            }
        ),
        encoding="utf-8",
    )
    outside = auth_isolated_root / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(account_lifecycle.AccountLifecycleForbidden):
        account_lifecycle.delete_account("unsafe@example.com", actor_id="u_admin")

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_failed_completion_cannot_delete_reregistered_incarnation(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import account_lifecycle

    _, first = identity.create_user_if_absent("aba@example.com", "hash-a", role="user")
    original_connect = account_lifecycle._connect
    fail_completion_once = True

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            nonlocal fail_completion_once
            if fail_completion_once and "SET status = 'completed'" in sql:
                fail_completion_once = False
                raise sqlite3.OperationalError("simulated completion-ledger failure")
            return self._connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    monkeypatch.setattr(
        account_lifecycle,
        "_connect",
        lambda: ConnectionProxy(original_connect()),
    )

    with pytest.raises(account_lifecycle.AccountLifecycleError):
        account_lifecycle.delete_account(
            "aba@example.com",
            expected_user_id=first["id"],
        )
    assert identity.get_user("aba@example.com") is None

    _, second = identity.create_user_if_absent("aba@example.com", "hash-b", role="user")
    assert second["id"] != first["id"]

    # An unbound retry cannot silently adopt B.
    with pytest.raises(account_lifecycle.AccountLifecycleConflict):
        account_lifecycle.delete_account("aba@example.com")

    # An explicitly bound recovery may finish A's ledger, but must leave B
    # and all of B's authentication state untouched.
    recovered = account_lifecycle.delete_account(
        "aba@example.com",
        expected_user_id=first["id"],
    )
    current = identity.get_user("aba@example.com")
    assert recovered.deleted is True
    assert current is not None
    assert current["id"] == second["id"]
    assert current["disabled"] is False


def test_audit_scrub_uses_writer_lock_and_preserves_other_accounts(
    auth_isolated_root,
) -> None:
    from deeptutor.multi_user import audit, paths
    from deeptutor.services import account_lifecycle

    audit._write({"user_id": "u_delete", "action": "read"})
    audit._write({"user_id": "u_keep", "action": "read"})
    audit_file = paths.SYSTEM_ROOT / "audit/usage.jsonl"

    account_lifecycle._scrub_jsonl(
        audit_file,
        user_id="u_delete",
        username="delete@example.com",
    )

    records = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert records == [{"user_id": "u_keep", "action": "read"}]
