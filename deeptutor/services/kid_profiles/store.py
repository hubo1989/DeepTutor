"""
Kid Profile Store — JSON Persistence
=====================================

Persists kid profiles to ``data/user/profiles.json``. The file contains
a list of all profiles, keyed internally by guardian_user_id + profile_id.

All writes use ``atomic_write_json`` for crash safety.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from deeptutor.services.file_io import atomic_write_json

_PROFILES_PATH = Path("data/user/profiles.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_raw() -> dict[str, Any]:
    """Load the raw JSON from disk. Returns empty dict if file is missing."""
    if not _PROFILES_PATH.exists():
        return {"profiles": []}
    try:
        data = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        if "profiles" not in data:
            data["profiles"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"profiles": []}


def _save_raw(data: dict[str, Any]) -> None:
    """Atomically write the full profiles JSON."""
    atomic_write_json(_PROFILES_PATH, data)


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    """Convert a KidProfile (or compatible dict) to a JSON-safe dict."""
    if isinstance(profile, dict):
        consent = profile.get("consent", {})
        return {
            "profile_id": profile.get("profile_id", ""),
            "guardian_user_id": profile.get("guardian_user_id", ""),
            "nickname": profile.get("nickname", ""),
            "avatar": profile.get("avatar", "🧒"),
            "age_band": profile.get("age_band", "7-9"),
            "role": profile.get("role", "kid"),
            "created_at": profile.get("created_at", ""),
            "consent": {
                "agreed": consent.get("agreed", False) if isinstance(consent, dict) else False,
                "agreed_at": consent.get("agreed_at", "") if isinstance(consent, dict) else "",
                "guardian_user_id": consent.get("guardian_user_id", "")
                if isinstance(consent, dict)
                else "",
            },
            "settings": profile.get("settings", {}),
        }

    from deeptutor.services.kid_profiles.models import KidProfile

    if isinstance(profile, KidProfile):
        return {
            "profile_id": profile.profile_id,
            "guardian_user_id": profile.guardian_user_id,
            "nickname": profile.nickname,
            "avatar": profile.avatar,
            "age_band": profile.age_band,
            "role": profile.role,
            "created_at": profile.created_at,
            "consent": {
                "agreed": profile.consent.agreed,
                "agreed_at": profile.consent.agreed_at,
                "guardian_user_id": profile.consent.guardian_user_id,
            },
            "settings": dict(profile.settings),
        }

    raise TypeError(f"Unsupported profile type: {type(profile)}")


def _dict_to_profile(data: dict[str, Any]) -> Any:
    """Convert a JSON dict to a KidProfile."""
    from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile

    consent_data = data.get("consent", {})
    return KidProfile(
        profile_id=data.get("profile_id", ""),
        guardian_user_id=data.get("guardian_user_id", ""),
        nickname=data.get("nickname", ""),
        avatar=data.get("avatar", "🧒"),
        age_band=data.get("age_band", "7-9"),
        role=data.get("role", "kid"),
        created_at=data.get("created_at", ""),
        consent=GuardianConsent(
            agreed=bool(consent_data.get("agreed", False))
            if isinstance(consent_data, dict)
            else False,
            agreed_at=consent_data.get("agreed_at", "") if isinstance(consent_data, dict) else "",
            guardian_user_id=consent_data.get("guardian_user_id", "")
            if isinstance(consent_data, dict)
            else "",
        ),
        settings=dict(data.get("settings", {})),
    )


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------


def load_all(guardian_user_id: str) -> list[Any]:
    """
    Load all kid profiles for a given guardian.

    Args:
        guardian_user_id: The parent/guardian's user ID.

    Returns:
        List of KidProfile objects belonging to this guardian.
    """
    raw = _load_raw()
    profiles = [
        _dict_to_profile(p)
        for p in raw.get("profiles", [])
        if p.get("guardian_user_id") == guardian_user_id
    ]
    return profiles


def get(profile_id: str) -> Any | None:
    """
    Load a single profile by ID.

    Args:
        profile_id: The profile identifier.

    Returns:
        The KidProfile, or None if not found.
    """
    raw = _load_raw()
    for p in raw.get("profiles", []):
        if p.get("profile_id") == profile_id:
            return _dict_to_profile(p)
    return None


def create(profile: Any) -> Any:
    """
    Create a new kid profile. Generates a profile_id and sets created_at.

    Args:
        profile: A KidProfile object (profile_id will be auto-generated if empty).

    Returns:
        The created KidProfile with generated fields.
    """
    from deeptutor.services.kid_profiles.models import KidProfile

    if not isinstance(profile, KidProfile):
        raise TypeError("Expected KidProfile instance")

    if not profile.profile_id:
        profile.profile_id = f"kid-{uuid4().hex[:12]}"
    if not profile.created_at:
        profile.created_at = _now_iso()
    profile.role = "kid"

    raw = _load_raw()
    raw["profiles"].append(_profile_to_dict(profile))
    _save_raw(raw)

    return profile


def delete(profile_id: str) -> bool:
    """
    Delete a profile by ID.

    Args:
        profile_id: The profile identifier.

    Returns:
        True if a profile was deleted, False if not found.
    """
    raw = _load_raw()
    original_len = len(raw["profiles"])
    raw["profiles"] = [p for p in raw["profiles"] if p.get("profile_id") != profile_id]
    if len(raw["profiles"]) < original_len:
        _save_raw(raw)
        return True
    return False


def update(profile: Any) -> Any | None:
    """
    Update an existing profile in place.

    Args:
        profile: A KidProfile with updated fields.

    Returns:
        The updated profile, or None if not found.
    """
    from deeptutor.services.kid_profiles.models import KidProfile

    if not isinstance(profile, KidProfile):
        raise TypeError("Expected KidProfile instance")

    raw = _load_raw()
    for i, p in enumerate(raw["profiles"]):
        if p.get("profile_id") == profile.profile_id:
            raw["profiles"][i] = _profile_to_dict(profile)
            _save_raw(raw)
            return profile
    return None


__all__ = ["load_all", "get", "create", "delete", "update"]
