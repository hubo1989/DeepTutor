from __future__ import annotations

import sqlite3

import pytest


def test_failed_login_limit_is_persistent_across_username_ip_pairs(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.services import login_rate_limit

    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_WINDOW_SECONDS", "900")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_LOCKOUT_SECONDS", "900")

    login_rate_limit.record_login_result("Alice@Example.com", "203.0.113.4", success=False)
    login_rate_limit.record_login_result("alice@example.com", "203.0.113.4", success=False)

    with pytest.raises(login_rate_limit.LoginRateLimited) as blocked:
        login_rate_limit.check_login_allowed("ALICE@example.com", "203.0.113.4")
    assert blocked.value.retry_after > 0

    # The username bucket follows the account across source IP rotation, while
    # an unrelated account at a fresh IP remains independent.
    with pytest.raises(login_rate_limit.LoginRateLimited):
        login_rate_limit.check_login_allowed("alice@example.com", "203.0.113.5")
    login_rate_limit.check_login_allowed("bob@example.com", "203.0.113.4")

    # The limiter opens a durable SQLite store on each operation, so a fresh
    # object/process sees the same lockout instead of resetting in memory.
    assert (auth_isolated_root / "data/system/auth/login_rate_limit.sqlite3").exists()
    with pytest.raises(login_rate_limit.LoginRateLimited):
        login_rate_limit.check_login_allowed("alice@example.com", "203.0.113.4")


def test_successful_login_clears_pair_failures(auth_isolated_root, monkeypatch) -> None:
    from deeptutor.services import login_rate_limit

    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES", "3")
    login_rate_limit.record_login_result("alice", "127.0.0.1", success=False)
    login_rate_limit.record_login_result("alice", "127.0.0.1", success=True)

    login_rate_limit.check_login_allowed("alice", "127.0.0.1")


def test_single_ip_cannot_bypass_limit_by_spraying_usernames(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.services import login_rate_limit

    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_PAIR", "100")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_USERNAME", "100")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_IP", "3")
    for username in ("alice", "bob", "carol"):
        login_rate_limit.record_login_result(username, "203.0.113.10", success=False)

    with pytest.raises(login_rate_limit.LoginRateLimited):
        login_rate_limit.check_login_allowed("dave", "203.0.113.10")


def test_account_cannot_bypass_limit_by_rotating_ips(auth_isolated_root, monkeypatch) -> None:
    from deeptutor.services import login_rate_limit

    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_PAIR", "100")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_USERNAME", "3")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_IP", "100")
    for client_ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        login_rate_limit.record_login_result("alice", client_ip, success=False)

    with pytest.raises(login_rate_limit.LoginRateLimited):
        login_rate_limit.check_login_allowed("alice", "203.0.113.99")


def test_success_does_not_clear_ip_failures_for_other_accounts(
    auth_isolated_root, monkeypatch
) -> None:
    from deeptutor.services import login_rate_limit

    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_PAIR", "100")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_USERNAME", "100")
    monkeypatch.setenv("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_IP", "2")
    login_rate_limit.record_login_result("alice", "203.0.113.10", success=False)
    login_rate_limit.record_login_result("alice", "203.0.113.10", success=True)
    login_rate_limit.record_login_result("bob", "203.0.113.10", success=False)

    with pytest.raises(login_rate_limit.LoginRateLimited):
        login_rate_limit.check_login_allowed("carol", "203.0.113.10")


def test_auth_disabled_login_bypasses_persistent_limiter(monkeypatch) -> None:
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", False)
    payload = auth_service.authenticate("", "")

    assert payload is not None
    assert payload.role == "admin"
    assert payload.user_id == "local-admin"


def test_overlong_username_is_stored_only_as_fixed_digest(auth_isolated_root) -> None:
    from deeptutor.services import login_rate_limit

    oversized = "用" * 100_000
    login_rate_limit.record_login_result(oversized, "203.0.113.1", success=False)

    with sqlite3.connect(
        auth_isolated_root / "data/system/auth/login_rate_limit.sqlite3"
    ) as connection:
        usernames = [
            row[0]
            for row in connection.execute(
                "SELECT username FROM login_rate_limit_buckets WHERE username <> ''"
            )
        ]
    assert usernames
    assert all(value.startswith("sha256:") and len(value) == 71 for value in usernames)
