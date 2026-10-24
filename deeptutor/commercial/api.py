"""Authenticated commercial account read API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.services.auth import TokenPayload

from .access import get_commercial_access, require_commercial_access
from .models import thaw_json
from .runtime import get_commercial_runtime

router = APIRouter()


class CommercialMeterUsage(BaseModel):
    """One quota meter's consumption inside its current window."""

    limit: int | None
    window: str
    used: int
    reserved: int


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
    usage: dict[str, CommercialMeterUsage] = Field(default_factory=dict)


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
    usage: dict[str, Any] = {}
    if resolved.subscription_id is not None:
        control_plane = getattr(runtime, "control_plane", None)
        if control_plane is not None:
            usage = await control_plane.usage_summary(resolved.customer_id)
    return {
        "enabled": True,
        "is_admin": False,
        "status": resolved.status.value if resolved.status is not None else None,
        "valid_until": resolved.valid_until,
        "plan_version_id": resolved.plan_version_id,
        "values": thaw_json(resolved.values),
        "usage": usage,
    }


@router.get("/commercial/usage")
async def get_my_commercial_usage(
    _payload: TokenPayload | None = Depends(require_commercial_access),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    meter: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the caller's usage events with per-day/per-model rollups."""

    runtime = get_commercial_runtime()
    if not runtime.settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commercial usage accounting is not enabled.",
        )
    user = get_current_user()
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrators are not metered; nothing to report.",
        )
    access = get_commercial_access()
    if access is None or access.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Subscription state is temporarily unavailable.",
        )
    control_plane = runtime.control_plane
    if control_plane is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Subscription state is temporarily unavailable.",
        )
    if since is not None and since.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="since must carry a timezone offset",
        )
    if until is not None and until.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="until must carry a timezone offset",
        )
    return await control_plane.usage_details(
        access.resolved.customer_id,
        since=since,
        until=until,
        meter=meter,
    )


__all__ = ["CommercialMeResponse", "router"]
