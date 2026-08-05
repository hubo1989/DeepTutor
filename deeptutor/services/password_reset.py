"""One-time, durable email challenges for local password resets."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
import time

from deeptutor.multi_user import identity
from deeptutor.services import email_verification
from deeptutor.services.private_state import (
    ensure_private_file,
    harden_sqlite_files,
)

_WINDOW_SECONDS = 3600
_MAX_ROWS = 100_000
_CODE_DIGITS = 6
logger = logging.getLogger(__name__)


class PasswordResetError(RuntimeError):
    """Base class for expected password-reset failures."""


class PasswordResetRateLimited(PasswordResetError):
    """The mailbox or client IP exceeded the challenge limit."""


class PasswordResetUnavailable(PasswordResetError):
    """The challenge ledger or email delivery is unavailable."""


@dataclass(frozen=True, slots=True)
class PasswordResetChallenge:
    email: str
    code: str
    expires_at: float


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _db_path():
    return identity.AUTH_DIR / "password_reset.sqlite3"


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
            CREATE TABLE IF NOT EXISTS password_reset_challenges (
                email TEXT PRIMARY KEY,
                code_digest TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                client_ip TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS password_reset_rate_limits (
                subject TEXT PRIMARY KEY,
                window_started REAL NOT NULL,
                sent_count INTEGER NOT NULL,
                last_sent_at REAL NOT NULL
            );
            """
        )
        harden_sqlite_files(path)
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise PasswordResetUnavailable("Password reset service is unavailable") from exc


def _auth_secret() -> bytes:
    from deeptutor.services import auth as auth_service

    secret = str(getattr(auth_service, "AUTH_SECRET", "") or "")
    if not secret:
        secret = identity.load_or_create_auth_secret()
    return secret.encode("utf-8")


def _digest(email: str, code: str, issued_at: float) -> str:
    payload = f"password-reset\x00{email}\x00{issued_at:.6f}\x00{code}".encode()
    return hmac.new(_auth_secret(), payload, hashlib.sha256).hexdigest()


def _consume_rate_limit(
    connection: sqlite3.Connection,
    *,
    subject: str,
    now: float,
    limit: int,
    cooldown: int,
) -> None:
    row = connection.execute(
        "SELECT window_started, sent_count, last_sent_at "
        "FROM password_reset_rate_limits WHERE subject = ?",
        (subject,),
    ).fetchone()
    if row is None or now - float(row["window_started"]) >= _WINDOW_SECONDS:
        connection.execute(
            "INSERT OR REPLACE INTO password_reset_rate_limits "
            "(subject, window_started, sent_count, last_sent_at) VALUES (?, ?, 1, ?)",
            (subject, now, now),
        )
        return
    if int(row["sent_count"]) >= limit or now - float(row["last_sent_at"]) < cooldown:
        raise PasswordResetRateLimited("Password reset request limit exceeded")
    connection.execute(
        "UPDATE password_reset_rate_limits "
        "SET sent_count = sent_count + 1, last_sent_at = ? WHERE subject = ?",
        (now, subject),
    )


def issue_challenge(email: str, client_ip: str) -> PasswordResetChallenge:
    canonical = email_verification.normalize_email(email)
    if not canonical:
        raise ValueError("email is required")
    now = time.time()
    ttl = _env_int("DEEPTUTOR_PASSWORD_RESET_TTL_MINUTES", 10, low=5, high=60) * 60
    cooldown = _env_int("DEEPTUTOR_PASSWORD_RESET_COOLDOWN_SECONDS", 60, low=30, high=3600)
    email_limit = _env_int("DEEPTUTOR_PASSWORD_RESET_MAX_PER_EMAIL_HOUR", 5, low=1, high=50)
    ip_limit = _env_int("DEEPTUTOR_PASSWORD_RESET_MAX_PER_IP_HOUR", 30, low=5, high=200)
    safe_ip = str(client_ip or "unknown")[:128] or "unknown"
    code = f"{secrets.randbelow(10**_CODE_DIGITS):0{_CODE_DIGITS}d}"
    expires_at = now + ttl
    digest = _digest(canonical, code, now)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM password_reset_challenges WHERE expires_at <= ?", (now,))
        connection.execute(
            "DELETE FROM password_reset_rate_limits WHERE last_sent_at <= ?",
            (now - 2 * _WINDOW_SECONDS,),
        )
        row_count = int(
            connection.execute("SELECT COUNT(*) FROM password_reset_rate_limits").fetchone()[0]
        )
        if row_count >= _MAX_ROWS:
            raise PasswordResetRateLimited("Password reset service capacity reached")
        _consume_rate_limit(
            connection,
            subject=f"email:{canonical}",
            now=now,
            limit=email_limit,
            cooldown=cooldown,
        )
        _consume_rate_limit(
            connection,
            subject=f"ip:{safe_ip}",
            now=now,
            limit=ip_limit,
            # The IP bucket stops high-volume spraying but does not impose the
            # per-mailbox resend cooldown on every user behind one NAT/proxy.
            cooldown=0,
        )
        connection.execute(
            "INSERT OR REPLACE INTO password_reset_challenges "
            "(email, code_digest, issued_at, expires_at, attempts, client_ip) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (canonical, digest, now, expires_at, safe_ip),
        )
        connection.execute("COMMIT")
    except PasswordResetRateLimited:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise PasswordResetUnavailable("Could not issue password reset challenge") from exc
    finally:
        connection.close()
    return PasswordResetChallenge(canonical, code, expires_at)


def discard_challenge(email: str) -> None:
    if not _db_path().exists():
        return
    connection = _connect()
    try:
        connection.execute(
            "DELETE FROM password_reset_challenges WHERE email = ?",
            (email_verification.normalize_email(email),),
        )
    finally:
        connection.close()


def consume_challenge(email: str, code: str) -> bool:
    canonical = email_verification.normalize_email(email)
    supplied = str(code or "").strip()
    max_attempts = _env_int("DEEPTUTOR_PASSWORD_RESET_MAX_ATTEMPTS", 5, low=3, high=10)
    now = time.time()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT code_digest, issued_at, expires_at, attempts "
            "FROM password_reset_challenges WHERE email = ?",
            (canonical,),
        ).fetchone()
        if row is None or now >= float(row["expires_at"]):
            if row is not None:
                connection.execute(
                    "DELETE FROM password_reset_challenges WHERE email = ?", (canonical,)
                )
            connection.execute("COMMIT")
            return False
        expected = _digest(canonical, supplied, float(row["issued_at"]))
        if not hmac.compare_digest(expected, str(row["code_digest"])):
            attempts = int(row["attempts"]) + 1
            if attempts >= max_attempts:
                connection.execute(
                    "DELETE FROM password_reset_challenges WHERE email = ?", (canonical,)
                )
            else:
                connection.execute(
                    "UPDATE password_reset_challenges SET attempts = ? WHERE email = ?",
                    (attempts, canonical),
                )
            connection.execute("COMMIT")
            return False
        connection.execute("DELETE FROM password_reset_challenges WHERE email = ?", (canonical,))
        connection.execute("COMMIT")
        return True
    except (OSError, sqlite3.Error) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise PasswordResetUnavailable("Could not verify password reset challenge") from exc
    finally:
        connection.close()


def send_password_reset_email(challenge: PasswordResetChallenge) -> None:
    config = email_verification.smtp_config()
    if not config.configured:
        raise PasswordResetUnavailable("SMTP is not configured")
    minutes = max(1, int((challenge.expires_at - time.time()) // 60))
    message = EmailMessage()
    message["Subject"] = "导学吧密码重置 / LearnLeader password reset"
    message["From"] = config.sender
    message["To"] = challenge.email
    message.set_content(
        "你的导学吧密码重置验证码是：{code}\n\n"
        "验证码 {minutes} 分钟内有效。若不是你本人操作，请忽略此邮件。\n\n"
        "Your LearnLeader password reset code is: {code}\n\n"
        "It expires in {minutes} minutes. If you did not request this, ignore this email.".format(
            code=challenge.code, minutes=minutes
        )
    )
    try:
        if config.use_ssl:
            with smtplib.SMTP_SSL(
                config.host, config.port, timeout=config.timeout_seconds
            ) as client:
                if config.username:
                    client.login(config.username, config.password)
                client.send_message(message)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds) as client:
                client.ehlo()
                if config.use_tls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if config.username:
                    client.login(config.username, config.password)
                client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise PasswordResetUnavailable("SMTP delivery failed") from exc


def deliver_password_reset_challenge(
    challenge: PasswordResetChallenge,
    *,
    deliverable: bool,
) -> None:
    """Deliver asynchronously without leaking account state to the request.

    This callable is deliberately safe for ``BackgroundTasks``: delivery
    errors are logged without mailbox/code details and never alter the already
    emitted public 202 response.  Undeliverable and failed challenges are
    removed so a code can never be guessed for a non-account.
    """
    if not deliverable:
        discard_challenge(challenge.email)
        return
    try:
        send_password_reset_email(challenge)
    except Exception:
        logger.warning("Password reset email delivery failed", exc_info=True)
        try:
            discard_challenge(challenge.email)
        except Exception:
            logger.error("Could not discard a failed password reset challenge", exc_info=True)


def email_delivery_configured() -> bool:
    return email_verification.email_delivery_configured()


def clear_account_state(email: str) -> None:
    """Erase pending challenges and email-keyed limiter state."""
    canonical = email_verification.normalize_email(email)
    if not canonical or not _db_path().exists():
        return
    connection = _connect()
    try:
        connection.execute("DELETE FROM password_reset_challenges WHERE email = ?", (canonical,))
        connection.execute(
            "DELETE FROM password_reset_rate_limits WHERE subject = ?",
            (f"email:{canonical}",),
        )
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    finally:
        connection.close()


__all__ = [
    "PasswordResetChallenge",
    "PasswordResetError",
    "PasswordResetRateLimited",
    "PasswordResetUnavailable",
    "clear_account_state",
    "consume_challenge",
    "discard_challenge",
    "deliver_password_reset_challenge",
    "email_delivery_configured",
    "issue_challenge",
    "send_password_reset_email",
]
