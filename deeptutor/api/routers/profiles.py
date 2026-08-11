"""
Profiles API Router — Kid profile CRUD + role switching.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.kid_context import KidContext, set_kid_context
from deeptutor.multi_user.models import CurrentUser
from deeptutor.services.gamification.store import init_if_absent
from deeptutor.services.kid_profiles.guard import require_guardian
from deeptutor.services.kid_profiles.models import GuardianConsent, KidProfile
from deeptutor.services.kid_profiles.pin import has_pin, set_pin, verify_pin
from deeptutor.services.kid_profiles.store import (
    create as create_profile,
)
from deeptutor.services.kid_profiles.store import (
    delete as delete_profile,
)
from deeptutor.services.kid_profiles.store import (
    get as get_profile,
)
from deeptutor.services.kid_profiles.store import (
    load_all as load_all_profiles,
)
from deeptutor.services.login_rate_limit import (
    LoginRateLimited,
    check_login_allowed,
    record_login_result,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic)
# ---------------------------------------------------------------------------


class CreateProfileRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=50)
    avatar: str = Field(default="🧒", max_length=100)
    age_band: str = Field(default="7-9", max_length=20)
    consent_agreed: bool = Field(..., description="Guardian consent required (COPPA)")


class ProfileResponse(BaseModel):
    profile_id: str
    guardian_user_id: str
    nickname: str
    avatar: str
    age_band: str
    role: str
    created_at: str


class SwitchProfileRequest(BaseModel):
    profile_id: str = Field(..., description="The profile to switch to/from")
    direction: Literal["to_kid", "to_guardian"]
    pin: str | None = Field(default=None, description="Required for to_guardian")


class SwitchProfileResponse(BaseModel):
    profile_id: str
    role: str
    nickname: str = ""
    age_band: str = ""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _profile_to_response(profile: KidProfile) -> ProfileResponse:
    return ProfileResponse(
        profile_id=profile.profile_id,
        guardian_user_id=profile.guardian_user_id,
        nickname=profile.nickname,
        avatar=profile.avatar,
        age_band=profile.age_band,
        role=profile.role,
        created_at=profile.created_at,
    )


def _get_client_ip(request: Request) -> str:
    """Extract the client IP, preferring X-Forwarded-For when present."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/profiles", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_kid_profile(
    body: CreateProfileRequest,
    guardian: CurrentUser = Depends(require_guardian),
) -> ProfileResponse:
    """
    Create a new kid profile.

    Requires guardian consent (COPPA compliance). The consent timestamp
    is captured automatically.
    """
    if not body.consent_agreed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Guardian consent is required to create a child profile.",
        )

    now = datetime.now(timezone.utc).isoformat()
    profile = KidProfile(
        guardian_user_id=guardian.id,
        nickname=body.nickname,
        avatar=body.avatar,
        age_band=body.age_band,
        role="kid",
        consent=GuardianConsent(
            agreed=True,
            agreed_at=now,
            guardian_user_id=guardian.id,
        ),
    )
    created = create_profile(profile)

    # Initialize gamification state for the new profile
    init_if_absent(created.profile_id, {"daily_goal_xp": 100})

    return _profile_to_response(created)


@router.get("/profiles", response_model=list[ProfileResponse])
async def list_profiles(
    guardian: CurrentUser = Depends(require_guardian),
) -> list[ProfileResponse]:
    """List all kid profiles for the current guardian."""
    profiles = load_all_profiles(guardian.id)
    return [_profile_to_response(p) for p in profiles]


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kid_profile(
    profile_id: str,
    guardian: CurrentUser = Depends(require_guardian),
) -> None:
    """Delete a kid profile."""
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        )
    if profile.guardian_user_id != guardian.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own profiles.",
        )
    delete_profile(profile_id)


@router.post("/profiles/switch", response_model=SwitchProfileResponse)
async def switch_profile(
    body: SwitchProfileRequest,
    request: Request,
) -> SwitchProfileResponse:
    """
    Switch between kid mode and guardian mode.

    - ``to_kid``: No PIN required. Sets the active profile to the kid.
    - ``to_guardian``: Requires a valid PIN. Returns the user to guardian mode.
    """
    user = get_current_user()
    client_ip = _get_client_ip(request)

    if body.direction == "to_kid":
        profile = get_profile(body.profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profile not found.",
            )
        if profile.guardian_user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This profile does not belong to your account.",
            )

        ctx = KidContext(
            profile_id=profile.profile_id,
            role="kid",
            guardian_user_id=user.id,
            age_band=profile.age_band,
        )
        set_kid_context(ctx)

        return SwitchProfileResponse(
            profile_id=profile.profile_id,
            role="kid",
            nickname=profile.nickname,
            age_band=profile.age_band,
        )

    # direction == "to_guardian"
    if body.pin is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PIN is required to switch back to guardian mode.",
        )

    # Rate-limit PIN attempts
    username = user.username or user.id
    try:
        check_login_allowed(username, client_ip)
    except LoginRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Retry after {exc.retry_after} seconds.",
            headers={"Retry-After": str(exc.retry_after)},
        )

    verified = verify_pin(user.id, body.pin)
    record_login_result(username, client_ip, success=verified)

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect PIN.",
        )

    ctx = KidContext(
        profile_id="",
        role="guardian",
        guardian_user_id=user.id,
        age_band="",
    )
    set_kid_context(ctx)

    return SwitchProfileResponse(
        profile_id="",
        role="guardian",
    )


@router.post("/profiles/pin", status_code=status.HTTP_200_OK)
async def set_guardian_pin(
    body: dict,
    guardian: CurrentUser = Depends(require_guardian),
) -> dict:
    """Set or update the guardian PIN."""
    pin = body.get("pin", "")
    if not pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PIN is required.",
        )
    try:
        set_pin(guardian.id, pin)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"status": "ok", "has_pin": True}


@router.get("/profiles/pin/status")
async def pin_status(
    guardian: CurrentUser = Depends(require_guardian),
) -> dict:
    """Check whether a PIN has been set."""
    return {"has_pin": has_pin(guardian.id)}


__all__ = ["router"]
