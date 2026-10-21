"""FastAPI dependency that resolves fresh commercial access per request."""

from __future__ import annotations

import logging

from fastapi import Cookie, Depends, Header, HTTPException, status

from deeptutor.multi_user.context import (
    get_current_user,
    set_current_user,
    user_from_token_payload,
)
from deeptutor.services.auth import AUTH_ENABLED, TokenPayload, decode_token

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


async def _require_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None, alias="dt_token"),
) -> TokenPayload | None:
    """Local auth dependency (avoids a domain -> API adapter import)."""

    if not AUTH_ENABLED:
        return None
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization
        else dt_token
    )
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    set_current_user(user_from_token_payload(payload))
    return payload



async def require_commercial_access(
    payload: TokenPayload | None = Depends(_require_auth),
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
