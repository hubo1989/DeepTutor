"""FastAPI dependency that resolves fresh commercial access per request."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, status

from deeptutor.api.routers.auth import require_auth
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.auth import TokenPayload

from .entitlement_context import (
    CommercialAccess,
    get_commercial_access,
    install_commercial_access_for_current_user,
    project_grant_for_commercial,
    reset_commercial_access,
    resolve_commercial_access_for_owner,
    set_commercial_access,
)
from .runtime import get_commercial_runtime

logger = logging.getLogger(__name__)


async def require_commercial_access(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload | None:
    """Authenticate and install fresh plan entitlements for this HTTP request."""

    runtime = get_commercial_runtime()
    try:
        await install_commercial_access_for_current_user(runtime=runtime)
    except Exception as exc:
        user = get_current_user()
        logger.exception("Commercial access resolution failed for owner %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Subscription service is temporarily unavailable.",
        ) from exc
    return payload


__all__ = [
    "CommercialAccess",
    "get_commercial_access",
    "project_grant_for_commercial",
    "require_commercial_access",
    "reset_commercial_access",
    "resolve_commercial_access_for_owner",
    "set_commercial_access",
]
