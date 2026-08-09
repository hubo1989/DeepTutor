"""
Kid Profiles Service — module exports.
"""

from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile
from deeptutor.services.kid_profiles.store import (
    create,
    delete,
    get,
    load_all,
    update,
)
from deeptutor.services.kid_profiles.pin import (
    has_pin,
    set_pin,
    verify_pin,
)
from deeptutor.services.kid_profiles.guard import (
    ROLE_CAPABILITIES,
    check_capability,
    require_capability,
    require_guardian,
    require_kid_or_guardian,
)

__all__ = [
    # Models
    "GuardianConsent",
    "KidProfile",
    # Store
    "create",
    "delete",
    "get",
    "load_all",
    "update",
    # PIN
    "has_pin",
    "set_pin",
    "verify_pin",
    # Guard
    "ROLE_CAPABILITIES",
    "check_capability",
    "require_capability",
    "require_guardian",
    "require_kid_or_guardian",
]
