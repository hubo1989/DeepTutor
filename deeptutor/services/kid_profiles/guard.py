"""
Role Guard — Capability-Based Access Control
=============================================

Defines the capability matrix for guardian vs. kid roles and provides
FastAPI dependency functions for route-level authorization.

Guardian (parent) has full access to all features.
Kid has restricted access: learning quests only, no account management,
no payments, no sharing. Safety filter is always-on, and sessions
are time-limited (default 20 minutes).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

# ---------------------------------------------------------------------------
# Role capability matrix
# ---------------------------------------------------------------------------

ROLE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "guardian": {
        "kid_quest": True,
        "profile_manage": True,
        "kb_crud": True,
        "payment": True,
        "share": True,
        "parent_panel": True,
        "settings": True,
        "safety_filter": "optional",  # guardian can toggle
        "session_limit": None,  # no limit
    },
    "kid": {
        "kid_quest": True,
        "profile_manage": False,
        "kb_crud": False,
        "payment": False,
        "share": False,
        "parent_panel": False,
        "settings": False,
        "safety_filter": "always_on",  # cannot be disabled
        "session_limit": 1200,  # 20 minutes (seconds)
    },
}

# Treat admin as equivalent to guardian for capability purposes
# (admins are the local-host superuser).
_ADMIN_ALIAS = "guardian"


def _resolve_role(user: CurrentUser) -> str:
    """Resolve the effective role, mapping admin → guardian."""
    if user.role == "admin":
        return _ADMIN_ALIAS
    return user.role


def check_capability(user: CurrentUser, capability: str) -> bool:
    """
    Check whether a user's role grants a specific capability.

    Args:
        user: The authenticated current user.
        capability: One of the keys in ``ROLE_CAPABILITIES``.

    Returns:
        True if the capability is granted (boolean True).
        For non-boolean values (e.g. "optional", None), returns the raw value
        cast to bool (so "optional" → True, None → False, integers → True).
    """
    effective_role = _resolve_role(user)
    caps = ROLE_CAPABILITIES.get(effective_role, {})
    value = caps.get(capability)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    # For strings like "optional", "always_on" → treat as available (True)
    return bool(value)


# ---------------------------------------------------------------------------
# FastAPI Dependency functions
# ---------------------------------------------------------------------------

def require_guardian() -> CurrentUser:
    """
    FastAPI dependency: ensure the current user is a guardian (or admin).

    Raises:
        HTTPException 403: If the user is not a guardian.
    """
    user = get_current_user()
    effective_role = _resolve_role(user)
    if effective_role != "guardian":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guardian access required for this action.",
        )
    return user


def require_kid_or_guardian() -> CurrentUser:
    """
    FastAPI dependency: allow both kid and guardian roles.

    This is used for endpoints accessible by children (like quests).

    Raises:
        HTTPException 403: If the user has an unrecognized role.
    """
    user = get_current_user()
    effective_role = _resolve_role(user)
    if effective_role not in ("guardian", "kid"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: unrecognized role.",
        )
    return user


def require_capability(capability: str) -> Any:
    """
    Factory: return a FastAPI dependency that checks a specific capability.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_capability("kb_crud"))])
        def admin_endpoint(): ...

    Returns:
        A callable suitable as a FastAPI dependency.
    """

    def _check() -> CurrentUser:
        user = get_current_user()
        if not check_capability(user, capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Capability '{capability}' is not available for your role.",
            )
        return user

    return _check


__all__ = [
    "ROLE_CAPABILITIES",
    "check_capability",
    "require_guardian",
    "require_kid_or_guardian",
    "require_capability",
]
