"""
Kid Profile Data Models
=======================

Dataclasses for the children/guardian profile system. A guardian account
(parent) can manage multiple kid profiles, each of which has its own
gamification state, safety settings, and role-based capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuardianConsent:
    """
    COPPA-compliant parental consent record.

    Must be captured at profile creation time. The guardian explicitly
    agrees to data collection for their child's learning experience.
    """

    agreed: bool = False
    agreed_at: str = ""  # ISO 8601 timestamp
    guardian_user_id: str = ""


@dataclass
class KidProfile:
    """
    A child's profile within the DeepTutor system.

    Attributes:
        profile_id: Unique identifier for this profile.
        guardian_user_id: The parent/guardian's user account ID.
        nickname: Display name for the child (child-friendly, no PII).
        avatar: Avatar identifier (emoji or image reference).
        age_band: Age range (e.g., "4-6", "7-9", "10-12").
        role: Always "kid" for child profiles.
        created_at: ISO 8601 creation timestamp.
        consent: Parental consent record (required for COPPA compliance).
        settings: Per-profile settings (theme, language, etc.).
    """

    profile_id: str = ""
    guardian_user_id: str = ""
    nickname: str = ""
    avatar: str = "🧒"
    age_band: str = "7-9"
    role: str = "kid"  # always "kid" for child profiles
    created_at: str = ""
    consent: GuardianConsent = field(default_factory=GuardianConsent)
    settings: dict[str, Any] = field(default_factory=dict)


__all__ = ["GuardianConsent", "KidProfile"]
