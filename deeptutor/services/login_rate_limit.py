"""Durable failed-login limiter keyed by canonical username and client IP."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sqlite3
import time

from deeptutor.multi_user import identity
from deeptutor.services.private_state import ensure_private_file, harden_sqlite_files

_MAX_ROWS = 100_000


class LoginRateLimitError(RuntimeError):
    """Base class for login limiter failures."""


class LoginRateLimited(LoginRateLimitError):
    """A pair, username, or IP bucket is temporarily locked."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__("Too many login attempts")


class LoginRateLimitUnavailable(LoginRateLimitError):
    """The persistent limiter could not be consulted safely."""


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _policy() -> tuple[int, int, int, int, int]:
    base = _env_int("DEEPTUTOR_LOGIN_MAX_FAILURES", 5, low=2, high=100)
    return (
        _env_int("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_PAIR", base, low=2, high=500),
        _env_int("DEEPTUTOR_LOGIN_MAX_FAILURES_PER_USERNAME", base, low=2, high=500),
        _env_int(
            "DEEPTUTOR_LOGIN_MAX_FAILURES_PER_IP",
            max(25, base * 10),
            low=2,
            high=5_000,
        ),
        _env_int("DEEPTUTOR_LOGIN_WINDOW_SECONDS", 900, low=60, high=86_400),
        _env_int("DEEPTUTOR_LOGIN_LOCKOUT_SECONDS", 900, low=60, high=86_400),
    )


def _canonical_username(username: str) -> str:
    value = str(username or "").strip()
    value = value.casefold() if "@" in value else value
    if len(value.encode("utf-8")) > 254:
        # The route rejects this input. Hashing here is a defense-in-depth
        # bound for non-HTTP callers and prevents attacker-controlled SQLite
        # growth if the service is reused directly.
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def _safe_ip(client_ip: str) -> str:
    return str(client_ip or "unknown").strip()[:128] or "unknown"


def _db_path() -> Path:
    return identity.AUTH_DIR / "login_rate_limit.sqlite3"


def _connect() -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        path = _db_path()
        ensure_private_file(path)
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA secure_delete = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS login_rate_limit_buckets (
                bucket_type TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                client_ip TEXT NOT NULL DEFAULT '',
                window_started REAL NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0,
                locked_until REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (bucket_type, username, client_ip)
            );
            CREATE INDEX IF NOT EXISTS idx_login_rate_limit_buckets_updated
                ON login_rate_limit_buckets (updated_at);
            """
        )
        # Migrate the short-lived pair-only v1 schema without losing active
        # lockouts. Dropping it prevents a later connection from resurrecting
        # rows cleared after a successful login.
        connection.execute("BEGIN IMMEDIATE")
        try:
            legacy = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'login_rate_limits'"
            ).fetchone()
            if legacy is not None:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO login_rate_limit_buckets
                        (bucket_type, username, client_ip, window_started,
                         failures, locked_until, updated_at)
                    SELECT 'pair', username, client_ip, window_started,
                           failures, locked_until, updated_at
                    FROM login_rate_limits
                    """
                )
                connection.execute("DROP TABLE login_rate_limits")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        harden_sqlite_files(path)
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise LoginRateLimitUnavailable("Login rate limiter is unavailable") from exc


def check_login_allowed(username: str, client_ip: str) -> None:
    """Raise when the pair, username, or IP bucket is currently locked."""
    canonical = _canonical_username(username)
    safe_ip = _safe_ip(client_ip)
    if not canonical:
        return
    _pair_limit, _username_limit, _ip_limit, window_seconds, lockout_seconds = _policy()
    now = time.time()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM login_rate_limit_buckets WHERE updated_at < ? AND locked_until < ?",
            (now - max(window_seconds, lockout_seconds) * 2, now),
        )
        rows = connection.execute(
            """
            SELECT locked_until FROM login_rate_limit_buckets
            WHERE (bucket_type = 'pair' AND username = ? AND client_ip = ?)
               OR (bucket_type = 'username' AND username = ? AND client_ip = '')
               OR (bucket_type = 'ip' AND username = '' AND client_ip = ?)
            """,
            (canonical, safe_ip, canonical, safe_ip),
        ).fetchall()
        locked_until = max((float(row["locked_until"]) for row in rows), default=0.0)
        if locked_until > now:
            retry_after = math.ceil(locked_until - now)
            connection.execute("COMMIT")
            raise LoginRateLimited(retry_after)
        connection.execute("COMMIT")
    except LoginRateLimited:
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise LoginRateLimitUnavailable("Login rate limiter check failed") from exc
    finally:
        connection.close()


def record_login_result(
    username: str,
    client_ip: str,
    *,
    success: bool,
) -> None:
    """Record one completed authentication attempt atomically."""
    canonical = _canonical_username(username)
    safe_ip = _safe_ip(client_ip)
    if not canonical:
        return
    pair_limit, username_limit, ip_limit, window_seconds, lockout_seconds = _policy()
    now = time.time()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM login_rate_limit_buckets WHERE updated_at < ? AND locked_until < ?",
            (now - max(window_seconds, lockout_seconds) * 2, now),
        )
        if success:
            connection.execute(
                "DELETE FROM login_rate_limit_buckets "
                "WHERE username = ? AND bucket_type IN ('pair', 'username')",
                (canonical,),
            )
            connection.execute("COMMIT")
            return

        for bucket_type, bucket_username, bucket_ip, limit in (
            ("pair", canonical, safe_ip, pair_limit),
            ("username", canonical, "", username_limit),
            ("ip", "", safe_ip, ip_limit),
        ):
            row = connection.execute(
                "SELECT window_started, failures FROM login_rate_limit_buckets "
                "WHERE bucket_type = ? AND username = ? AND client_ip = ?",
                (bucket_type, bucket_username, bucket_ip),
            ).fetchone()
            if row is None or now - float(row["window_started"]) >= window_seconds:
                window_started = now
                failures = 1
            else:
                window_started = float(row["window_started"])
                failures = int(row["failures"]) + 1
            locked_until = now + lockout_seconds if failures >= limit else 0.0
            connection.execute(
                """
                INSERT INTO login_rate_limit_buckets
                    (bucket_type, username, client_ip, window_started,
                     failures, locked_until, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_type, username, client_ip) DO UPDATE SET
                    window_started = excluded.window_started,
                    failures = excluded.failures,
                    locked_until = excluded.locked_until,
                    updated_at = excluded.updated_at
                """,
                (
                    bucket_type,
                    bucket_username,
                    bucket_ip,
                    window_started,
                    failures,
                    locked_until,
                    now,
                ),
            )
        count = int(
            connection.execute("SELECT COUNT(*) FROM login_rate_limit_buckets").fetchone()[0]
        )
        if count > _MAX_ROWS:
            # Never evict active lockouts to make room for attacker-created
            # keys. If only active rows remain, fail closed until they age out.
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise LoginRateLimitUnavailable("Login rate limiter capacity reached")
        connection.execute("COMMIT")
    except LoginRateLimitUnavailable:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise LoginRateLimitUnavailable("Login rate limiter update failed") from exc
    finally:
        connection.close()


def clear_login_history(username: str) -> None:
    """Remove limiter rows for an account after reset or erasure."""
    canonical = _canonical_username(username)
    if not canonical or not _db_path().exists():
        return
    connection = _connect()
    try:
        connection.execute("DELETE FROM login_rate_limit_buckets WHERE username = ?", (canonical,))
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    except (OSError, sqlite3.Error) as exc:
        raise LoginRateLimitUnavailable("Could not clear login history") from exc
    finally:
        connection.close()


__all__ = [
    "LoginRateLimitError",
    "LoginRateLimitUnavailable",
    "LoginRateLimited",
    "check_login_allowed",
    "clear_login_history",
    "record_login_result",
]
