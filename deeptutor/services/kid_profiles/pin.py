"""
PIN Service — Guardian PIN Management
=====================================

Manages bcrypt-hashed guardian PINs for switching from kid mode back to
guardian mode. PINs are stored as bcrypt hashes in
``data/user/settings/pin_<user_id>.json`` — never in plaintext.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bcrypt

from deeptutor.services.file_io import atomic_write_json

_PIN_DIR = Path("data/user/settings")

# Minimum and maximum PIN lengths
MIN_PIN_LENGTH: int = 4
MAX_PIN_LENGTH: int = 8


def _pin_path(guardian_user_id: str) -> Path:
    """Build the file path for a guardian's PIN hash."""
    safe_id = "".join(c for c in guardian_user_id if c.isalnum() or c in "-_")
    return _PIN_DIR / f"pin_{safe_id}.json"


def _hash_pin(plain: str) -> str:
    """Hash a plaintext PIN using bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pin(plain: str, hashed: str) -> bool:
    """Verify a plaintext PIN against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _load_pin_hash(guardian_user_id: str) -> str | None:
    """Load the stored PIN hash for a guardian. Returns None if not set."""
    path = _pin_path(guardian_user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("pin_hash")
    except (json.JSONDecodeError, OSError):
        return None


def _save_pin_hash(guardian_user_id: str, pin_hash: str) -> None:
    """Save a PIN hash to disk."""
    payload: dict[str, Any] = {"pin_hash": pin_hash}
    atomic_write_json(_pin_path(guardian_user_id), payload)


def set_pin(guardian_user_id: str, plain: str) -> None:
    """
    Set or update a guardian's PIN.

    Args:
        guardian_user_id: The guardian's user ID.
        plain: The plaintext PIN (4-8 digits).

    Raises:
        ValueError: If the PIN does not meet length requirements.
    """
    if not plain or not plain.strip():
        raise ValueError("PIN cannot be empty")
    if len(plain) < MIN_PIN_LENGTH or len(plain) > MAX_PIN_LENGTH:
        raise ValueError(f"PIN must be {MIN_PIN_LENGTH}-{MAX_PIN_LENGTH} characters")
    _save_pin_hash(guardian_user_id, _hash_pin(plain))


def verify_pin(guardian_user_id: str, plain: str) -> bool:
    """
    Verify a PIN against the stored hash.

    Args:
        guardian_user_id: The guardian's user ID.
        plain: The plaintext PIN to check.

    Returns:
        True if the PIN matches (or if no PIN is set — open mode).
        False if the PIN does not match.
    """
    stored_hash = _load_pin_hash(guardian_user_id)
    if stored_hash is None:
        # No PIN set → consider it valid (first-time / open mode)
        return True
    return _verify_pin(plain, stored_hash)


def has_pin(guardian_user_id: str) -> bool:
    """
    Check whether a guardian has set a PIN.

    Args:
        guardian_user_id: The guardian's user ID.

    Returns:
        True if a PIN hash file exists for this guardian.
    """
    return _load_pin_hash(guardian_user_id) is not None


def clear_pin(guardian_user_id: str) -> None:
    """Remove the PIN file for a guardian."""
    path = _pin_path(guardian_user_id)
    if path.exists():
        path.unlink(missing_ok=True)


__all__ = [
    "MIN_PIN_LENGTH",
    "MAX_PIN_LENGTH",
    "set_pin",
    "verify_pin",
    "has_pin",
    "clear_pin",
]
