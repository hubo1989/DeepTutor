"""Authenticated commercial account read API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.services.auth import TokenPayload

from .access import get_commercial_access, require_commercial_access
from .models import thaw_json
from .runtime import get_commercial_runtime

router = APIRouter()


class CommercialMeResponse(BaseModel):
    """Stable, sanitized read model consumed by the subscription UI."""

    enabled: bool
    is_admin: bool
    status: (
        Literal[
            "disabled",
            "admin",
            "trialing",
            "active",
            "past_due",
            "canceled",
            "expired",
        ]
        | None
    )
    valid_until: datetime | None
    plan_version_id: str | None
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/commercial/me", response_model=CommercialMeResponse)
async def get_my_commercial_access(
    _payload: TokenPayload | None = Depends(require_commercial_access),
):
    """Return the caller's derived plan state without provider credentials."""

    runtime = get_commercial_runtime()
    if not runtime.settings.enabled:
        return {
            "enabled": False,
            "is_admin": False,
            "status": "disabled",
            "valid_until": None,
            "plan_version_id": None,
            "values": {},
        }

    user = get_current_user()
    if user.is_admin:
        return {
            "enabled": True,
            "is_admin": True,
            "status": "admin",
            "valid_until": None,
            "plan_version_id": None,
            "values": {},
        }

    access = get_commercial_access()
    if access is None or access.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Subscription state is temporarily unavailable.",
        )
    resolved = access.resolved
    return {
        "enabled": True,
        "is_admin": False,
        "status": resolved.status.value if resolved.status is not None else None,
        "valid_until": resolved.valid_until,
        "plan_version_id": resolved.plan_version_id,
        "values": thaw_json(resolved.values),
    }


__all__ = ["CommercialMeResponse", "router"]
