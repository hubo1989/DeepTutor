"""Auth router — login, logout, status, registration, profile, and user-management endpoints."""

import asyncio
from contextvars import Token as _CtxToken
import logging
import re

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask

from deeptutor.commercial.runtime import commercial_mode_requested
from deeptutor.commercial.service import DEFAULT_TRIAL_DAYS
from deeptutor.services.config import load_auth_settings, load_system_settings
from deeptutor.services.config.origins import normalize_origin, normalize_origins


def _cookie_samesite(*, secure: bool, commercial: bool) -> str:
    """Choose the legacy cross-site policy or hosted SaaS CSRF boundary."""

    if commercial:
        # Hosted commercial deployments must keep the frontend and API on the
        # same site. Lax prevents ambient cookies on cross-site POSTs while the
        # explicit CORS and WebSocket-Origin checks cover script/socket access.
        return "lax"
    # Legacy/self-hosted secure deployments may intentionally split the web
    # and API origins. SameSite=None preserves that behavior outside SaaS mode.
    return "none" if secure else "lax"


_SECURE = bool(load_auth_settings()["cookie_secure"])
_COMMERCIAL = commercial_mode_requested()
_SAMESITE = _cookie_samesite(
    secure=_SECURE,
    commercial=_COMMERCIAL,
)

from deeptutor.multi_user.context import (
    get_current_user_or_none,
    reset_current_user,
    set_current_user,
    user_from_token_payload,
)
from deeptutor.multi_user.paths import local_admin_user
from deeptutor.services import (
    account_lifecycle,
    email_verification,
    login_rate_limit,
    password_reset,
)
from deeptutor.services.auth import (
    AUTH_ENABLED,
    POCKETBASE_ENABLED,
    TOKEN_EXPIRE_HOURS,
    TokenPayload,
    add_user,
    add_verified_user,
    authenticate,
    authenticate_pb,
    create_token,
    decode_token,
    get_user_info,
    hash_password,
    list_users,
    register_pb,
    reset_password,
    set_avatar,
    set_role,
    validate_password_bytes,
)
from deeptutor.services.codex_auth.contracts import CodexAuthError
from deeptutor.services.codex_auth.service import deliver_codex_oauth_callback

logger = logging.getLogger(__name__)

router = APIRouter()

_COOKIE_NAME = "dt_token"
_COOKIE_MAX_AGE = TOKEN_EXPIRE_HOURS * 3600


def _cookie_attrs() -> dict:
    """Attribute set shared by ``login``'s ``set_cookie`` and ``logout``'s
    ``delete_cookie``.

    The deletion ``Set-Cookie`` must carry the same attributes as the one
    that created the cookie — ``delete_cookie`` defaults ``secure=False``,
    which browsers reject when paired with ``SameSite=None``, silently
    keeping the old cookie. See #623. Reads the module globals at call time
    so tests can monkeypatch ``_SECURE``/``_SAMESITE``.
    """
    return {
        "key": _COOKIE_NAME,
        "httponly": True,
        "samesite": _SAMESITE,
        "secure": _SECURE,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Payload for the POST /login endpoint."""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_bounded(cls, v: str) -> str:
        value = v.strip()
        if len(value.encode("utf-8")) > 254:
            raise ValueError("Username must be at most 254 UTF-8 bytes")
        return value

    @field_validator("password")
    @classmethod
    def password_bounded(cls, v: str) -> str:
        # Login intentionally has no minimum: legacy local accounts may use a
        # short password, and auth-disabled localhost mode accepts an empty one.
        return validate_password_bytes(v)


class RegisterRequest(BaseModel):
    """Payload for the POST /register endpoint."""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        import re

        v = v.strip()
        if not v:
            raise ValueError("Email cannot be empty")
        # Accept standard email addresses (used by PocketBase mode) or plain
        # usernames (used by the built-in SQLite/JSON auth mode).
        email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        plain_re = re.compile(r"^[A-Za-z0-9_\-.]{3,64}$")
        if len(v.encode("utf-8")) > 254 or (not email_re.match(v) and not plain_re.match(v)):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return validate_password_bytes(v)


class EmailRegistrationRequest(BaseModel):
    """Start a public registration by requesting a mailbox verification code."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = email_verification.normalize_email(v)
        if len(v) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return validate_password_bytes(v)


class VerifyRegistrationRequest(BaseModel):
    """Complete public registration with the one-time email code."""

    email: str
    code: str

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = email_verification.normalize_email(v)
        if len(v) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("code")
    @classmethod
    def code_valid(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Verification code must be 6 digits")
        return v


class PasswordResetRequest(BaseModel):
    """Request a one-time password-reset code for a mailbox."""

    email: str

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = email_verification.normalize_email(v)
        if len(v) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Enter a valid email address")
        return v


class ConfirmPasswordResetRequest(PasswordResetRequest):
    """Consume a reset code and choose a replacement password."""

    code: str
    new_password: str

    @field_validator("code")
    @classmethod
    def code_valid(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Verification code must be 6 digits")
        return v

    @field_validator("new_password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return validate_password_bytes(v)


class DeleteOwnAccountRequest(BaseModel):
    """Password confirmation for irreversible self-service deletion."""

    password: str

    @field_validator("password")
    @classmethod
    def password_bounded(cls, v: str) -> str:
        return validate_password_bytes(v)


class SetRoleRequest(BaseModel):
    """Payload for the PUT /users/{username}/role endpoint."""

    role: str

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'")
        return v


class AuthStatusResponse(BaseModel):
    """Response body for the GET /status endpoint."""

    enabled: bool
    authenticated: bool
    user_id: str | None = None
    username: str | None = None
    role: str | None = None
    is_admin: bool = False
    avatar: str = ""
    commercial_enabled: bool = False
    trial_days: int | None = None


class UserInfo(BaseModel):
    """Single user record returned by the GET /users and /profile endpoints."""

    id: str = ""
    username: str
    role: str
    created_at: str
    disabled: bool = False
    avatar: str = ""
    email_verified: bool = True


# Markers settable through PUT /profile. Image markers ("img:<version>") are
# managed exclusively by the upload endpoint so users cannot point their
# avatar at a file that was never validated.
_ICON_MARKER_RE = re.compile(r"^icon:[a-z0-9-]{1,32}:[a-z0-9-]{1,32}$")

# User ids are generated as "u_<uuid hex>" (plus the "local-admin" /
# "env-admin" sentinels); reject anything else before it reaches the
# filesystem layer.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UpdateProfileRequest(BaseModel):
    """Payload for the PUT /profile endpoint."""

    avatar: str

    @field_validator("avatar")
    @classmethod
    def avatar_valid(cls, v: str) -> str:
        v = v.strip()
        if v and not _ICON_MARKER_RE.match(v):
            raise ValueError("Avatar must be empty or 'icon:<name>:<color>'")
        return v


# ---------------------------------------------------------------------------
# Shared helper — extract token from cookie or Bearer header
# ---------------------------------------------------------------------------


def _bearer_token_from_header(authorization: str | None) -> str | None:
    """Parse ``Authorization: Bearer <token>`` without using ``HTTPBearer``.

    ``HTTPBearer`` is a class-based dependency whose ``__call__`` is annotated
    ``request: Request``. FastAPI doesn't inject a Request into WebSocket
    dependency resolution, which makes ``HTTPBearer`` raise ``TypeError`` the
    moment a router with this dep mounts a WS endpoint. Doing the parse by
    hand keeps ``require_auth`` HTTP/WS-symmetric.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


def _extract_token(authorization: str | None, dt_token: str | None) -> str | None:
    return _bearer_token_from_header(authorization) or dt_token


def _client_ip(request: Request) -> str:
    """Use the ASGI peer address; trusted proxy handling belongs to the server."""
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(username: str, client_ip: str) -> None:
    try:
        login_rate_limit.check_login_allowed(username, client_ip)
    except login_rate_limit.LoginRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except login_rate_limit.LoginRateLimitUnavailable as exc:
        logger.error("Persistent login limiter is unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is temporarily unavailable.",
        ) from exc


def _record_login_result(
    username: str,
    client_ip: str,
    *,
    success: bool,
) -> None:
    try:
        login_rate_limit.record_login_result(username, client_ip, success=success)
    except login_rate_limit.LoginRateLimitUnavailable as exc:
        logger.error("Could not persist login attempt: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is temporarily unavailable.",
        ) from exc


async def _provision_commercial_trial(user_id: str, role: str) -> bool:
    """Return whether a created regular account still needs trial repair."""
    if role == "admin" or not user_id:
        return False
    try:
        from deeptutor.commercial.runtime import get_commercial_runtime

        runtime = get_commercial_runtime()
        if not runtime.settings.enabled:
            return False
        await runtime.ensure_trial_for_owner(user_id)
        return False
    except Exception:
        # Identity creation is already committed.  Keep login usable and let
        # require_commercial_access's idempotent ensure call reconcile this
        # account when PostgreSQL becomes healthy again.
        logger.exception("Trial provisioning deferred for owner %s", user_id)
        return True


async def _commercial_export_data(user_id: str) -> dict | None:
    from deeptutor.commercial.errors import CommercialNotFound
    from deeptutor.commercial.runtime import get_commercial_runtime

    runtime = get_commercial_runtime()
    if not runtime.settings.enabled:
        return None
    if runtime.control_plane is None:
        raise RuntimeError("Commercial control plane is not available")
    try:
        return await runtime.control_plane.export_customer_data(user_id)
    except CommercialNotFound:
        # A legacy identity may not have reached reconciliation yet. There is
        # no commercial record to export, so the local export remains valid.
        return None
    except Exception as exc:
        raise RuntimeError("Commercial account export is unavailable") from exc


async def _erase_commercial_customer(user_id: str) -> None:
    from deeptutor.commercial.runtime import get_commercial_runtime

    runtime = get_commercial_runtime()
    if not runtime.settings.enabled:
        return
    if runtime.control_plane is None:
        raise RuntimeError("Commercial control plane is not available")
    await runtime.control_plane.erase_customer(user_id)


async def _delete_account_saga(
    username: str,
    *,
    expected_user_id: str | None,
    actor_id: str,
    actor_username: str,
) -> account_lifecycle.AccountDeletionResult:
    """Quiesce local work and erase billing before identity's commit point."""
    context = await asyncio.to_thread(
        account_lifecycle.begin_account_deletion,
        username,
        actor_id=actor_id,
        actor_username=actor_username,
        expected_user_id=expected_user_id,
    )
    if context.already_deleted:
        return await asyncio.to_thread(account_lifecycle.finish_account_deletion, context)
    try:
        from deeptutor.api.utils.task_id_manager import TaskIDManager
        from deeptutor.services.memory.consolidator.runs import get_run_manager
        from deeptutor.services.session.turn_runtime import cancel_turns_for_user

        await asyncio.gather(
            cancel_turns_for_user(context.user_id),
            TaskIDManager.get_instance().cancel_all_for_owner(context.user_id),
            get_run_manager().cancel_all_for_owner(context.user_id),
        )
        await asyncio.to_thread(
            account_lifecycle.remove_scheduled_account_jobs,
            context.user_id,
        )
        await _erase_commercial_customer(context.user_id)
    except Exception as exc:
        await asyncio.to_thread(
            account_lifecycle.fail_account_deletion,
            context,
            exc,
        )
        raise account_lifecycle.AccountLifecycleError(
            "Account deletion did not complete; the account remains disabled"
        ) from exc
    return await asyncio.to_thread(account_lifecycle.finish_account_deletion, context)


# ---------------------------------------------------------------------------
# Dependencies — reusable auth guards for other routers
# ---------------------------------------------------------------------------


def _install_current_user(payload: TokenPayload | None) -> _CtxToken:
    """Install the request-local current-user ContextVar from an auth result.

    Single point of truth for ``payload → CurrentUser`` so HTTP and WebSocket
    entry points produce identical user objects. ``payload is None`` means
    "no JWT was required" (AUTH_ENABLED=false) and resolves to the local
    admin user; a non-None payload resolves through ``user_from_token_payload``.

    Returns the ContextVar reset token. HTTP callers ignore it (the request
    ends with the task, so the var is GC'd with the task context). WebSocket
    callers keep it and call ``reset_current_user`` in their ``finally`` block,
    because a WS connection outlives the dependency-resolution task.

    ⚠ Invariant: every authenticated entry point MUST call this before the
    handler runs. Skipping it leaves ``get_current_path_service()`` falling
    back to the admin workspace — the silent-routing root cause of #481.
    """
    user = local_admin_user() if payload is None else user_from_token_payload(payload)
    return set_current_user(user)


async def require_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None),
) -> TokenPayload | None:
    """
    FastAPI dependency that enforces authentication when AUTH_ENABLED=true.

    Accepts the JWT from either:
      - Authorization: Bearer <token> header
      - dt_token cookie

    ``Header`` and ``Cookie`` are kept here in place of ``HTTPBearer`` so the
    function stays usable from WebSocket call sites that don't go through
    FastAPI's standard HTTP request lifecycle.

    Returns the authenticated TokenPayload, or None if auth is disabled.
    Raises HTTP 401 if auth is enabled but the token is missing or invalid.

    Declared ``async def`` so the ``set_current_user`` call runs in the same
    asyncio context as the endpoint. A sync dependency is dispatched via
    ``anyio.to_thread.run_sync``, which executes the function in a worker
    thread under a *copy* of the request context; any ``ContextVar.set``
    inside that thread is discarded when the thread returns, leaving the
    endpoint to read the unset default. That regression was the root cause
    of #481.
    """
    if not AUTH_ENABLED:
        _install_current_user(None)
        return None

    token = _extract_token(authorization, dt_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _install_current_user(payload)
    return payload


class _WsAuthFailed:
    """Sentinel: ws_require_auth failed and closed the WebSocket."""


ws_auth_failed: _WsAuthFailed = _WsAuthFailed()


def _configured_websocket_origins() -> set[str]:
    """Return the exact browser origins accepted by authenticated WS routes.

    WebSockets bypass ``CORSMiddleware``, so this intentionally mirrors
    ``api.main._build_cors_settings`` instead of relying on HTTP CORS headers.
    """
    system = load_system_settings()
    frontend_port = str(system["frontend_port"])
    origins = {
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
    origins.update(normalize_origins([system["cors_origin"], system["cors_origins"]]))
    return origins


def _websocket_origin_allowed(ws: WebSocket) -> bool:
    """Reject browser cross-site upgrades while allowing non-browser clients.

    Browsers always send ``Origin`` for WebSocket handshakes. CLI clients that
    authenticate with a Bearer token commonly omit it, which remains allowed.
    """
    if not AUTH_ENABLED:
        return True
    origin = str(getattr(ws, "headers", {}).get("origin") or "").strip()
    if not origin:
        return True
    normalized = normalize_origin(origin)
    allowed = _configured_websocket_origins()
    # A wildcard is never safe with authenticated cookies: it would recreate
    # the cross-site WebSocket hijacking path this check is meant to close.
    return bool(normalized and normalized in allowed and normalized != "*")


def _websocket_auth_token(ws: WebSocket) -> str | None:
    query_token = ws.query_params.get("token")
    authorization = getattr(ws, "headers", {}).get("authorization")
    return query_token or _bearer_token_from_header(authorization) or ws.cookies.get(_COOKIE_NAME)


def _websocket_principal_is_current(ws: WebSocket) -> bool:
    """Revalidate the socket JWT against the currently bound principal."""
    if not AUTH_ENABLED:
        return True
    raw_token = _websocket_auth_token(ws)
    expected = get_current_user_or_none()
    if not raw_token or expected is None:
        return False
    try:
        payload = decode_token(raw_token)
        if payload is None:
            return False
        current = user_from_token_payload(payload)
    except Exception:
        logger.warning("WebSocket token revalidation failed", exc_info=True)
        return False
    return (
        current.id == expected.id
        and current.username == expected.username
        and current.role == expected.role
        and current.scope.cache_key == expected.scope.cache_key
    )


async def _close_websocket(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        logger.debug("Failed to close WebSocket with code %s", code, exc_info=True)


async def _refresh_websocket_commercial_access(ws: WebSocket) -> bool:
    """Install a fresh subscription snapshot in this socket task.

    A WebSocket can outlive both a seven-day Trial boundary and an account
    state change.  Refreshing on the handshake and before every inbound frame
    makes subsequent consuming handlers observe current entitlements while
    still allowing already-running work to finish under its copied context.
    """
    try:
        from deeptutor.commercial.entitlement_context import (
            install_commercial_access_for_current_user,
        )

        await install_commercial_access_for_current_user()
        return True
    except Exception:
        logger.exception("Commercial access refresh failed for WebSocket principal")
        await _close_websocket(ws, 1011)
        return False


async def ws_require_capability_access(ws: WebSocket, capability: str = "llm") -> bool:
    """Fail the WS upgrade when the current user has no usable capability."""
    from deeptutor.multi_user.model_access import has_capability_access

    if has_capability_access(capability):
        return True
    await _close_websocket(ws, 4003)
    return False


async def ws_revalidate_identity(ws: WebSocket) -> bool:
    """Apply account deletion/disable/password reset before an inbound frame."""
    if not _websocket_principal_is_current(ws):
        await _close_websocket(ws, 4001)
        return False
    return await _refresh_websocket_commercial_access(ws)


async def ws_authorize_message(ws: WebSocket, capability: str = "llm") -> bool:
    """Revalidate identity and model access before a consuming WS action."""
    if not await ws_revalidate_identity(ws):
        return False
    return await ws_require_capability_access(ws, capability)


async def ws_require_auth(ws: WebSocket) -> _CtxToken | _WsAuthFailed:
    """Authenticate a WebSocket connection and set the user ContextVar.

    Must be called **before** ``ws.accept()`` so the server can reject
    unauthenticated upgrades cleanly.

    Returns a ContextVar reset token on success, or ``ws_auth_failed``
    on failure (the WebSocket is already closed — the caller should
    ``return`` immediately).

    Usage::

        user_token = await ws_require_auth(ws)
        if user_token is ws_auth_failed:
            return
        await ws.accept()
        try:
            ...
        finally:
            reset_current_user(user_token)
    """
    if not AUTH_ENABLED:
        user_token = _install_current_user(None)
        if await _refresh_websocket_commercial_access(ws):
            return user_token
        reset_current_user(user_token)
        return ws_auth_failed

    if not _websocket_origin_allowed(ws):
        await _close_websocket(ws, 4003)
        return ws_auth_failed

    token = _websocket_auth_token(ws)
    payload = decode_token(token) if token else None
    if not payload:
        await ws.close(code=4001)
        return ws_auth_failed

    user_token = _install_current_user(payload)
    if await _refresh_websocket_commercial_access(ws):
        return user_token
    reset_current_user(user_token)
    return ws_auth_failed


async def require_admin(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """
    FastAPI dependency that requires the caller to be an admin.

    Raises HTTP 403 if the authenticated user is not an admin.
    When AUTH_ENABLED=false, all requests are treated as admin.

    ``async def`` mirrors ``require_auth`` so the dependency chain stays on
    the event loop and the user ContextVar set by ``require_auth`` is visible
    to the endpoint.
    """
    if not AUTH_ENABLED:
        return _local_admin_token_payload()

    if payload is None or payload.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return payload


def _local_admin_token_payload() -> TokenPayload:
    """Synthetic admin payload used when AUTH_ENABLED=false.

    Mirrors the local admin identity (LOCAL_ADMIN_USERNAME / LOCAL_ADMIN_ID)
    so audit logs and self-reference checks behave the same as in multi-user
    mode. Values are kept aligned with ``local_admin_user()`` in
    ``deeptutor/multi_user/paths.py``.
    """
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME

    return TokenPayload(
        username=LOCAL_ADMIN_USERNAME,
        role="admin",
        user_id=LOCAL_ADMIN_ID,
    )


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.get("/openai-codex/callback")
async def receive_codex_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    headers = {"Cache-Control": "no-store"}
    try:
        callback_state = state if len(request.query_params.getlist("state")) == 1 else None
        await deliver_codex_oauth_callback(code, callback_state, error)
    except CodexAuthError as exc:
        return HTMLResponse(
            (
                "<!doctype html><title>LearnLeader Codex</title>"
                "<p>Authentication could not be received. Return to LearnLeader and try again.</p>"
            ),
            status_code=exc.http_status,
            headers=headers,
        )
    return HTMLResponse(
        (
            "<!doctype html><title>LearnLeader Codex</title>"
            "<p>Authentication received. You can return to LearnLeader.</p>"
        ),
        headers=headers,
    )


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None),
) -> AuthStatusResponse:
    """Return whether auth is enabled and whether the current request is authenticated."""
    if not AUTH_ENABLED:
        return AuthStatusResponse(
            enabled=False,
            authenticated=True,
            user_id="local-admin",
            username="local",
            role="admin",
            is_admin=True,
            commercial_enabled=False,
            trial_days=None,
        )

    token = _extract_token(authorization, dt_token)
    payload = decode_token(token) if token else None
    avatar = ""
    if payload is not None:
        info = get_user_info(payload.username)
        if info:
            avatar = str(info.get("avatar") or "")
    return AuthStatusResponse(
        enabled=True,
        authenticated=payload is not None,
        user_id=payload.user_id if payload else None,
        username=payload.username if payload else None,
        role=payload.role if payload else None,
        is_admin=payload.role == "admin" if payload else False,
        avatar=avatar,
        commercial_enabled=_COMMERCIAL,
        trial_days=DEFAULT_TRIAL_DAYS if _COMMERCIAL else None,
    )


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    """Validate credentials and set a JWT cookie."""
    if not AUTH_ENABLED:
        return {"ok": True, "message": "Auth is disabled — no login required."}

    client_ip = _client_ip(request)
    _check_login_rate_limit(body.username, client_ip)

    if POCKETBASE_ENABLED:
        # PocketBase mode: email = username field for backwards-compat with the
        # existing LoginRequest schema; users can pass their email as "username".
        pb_result = authenticate_pb(body.username, body.password)
        _record_login_result(body.username, client_ip, success=pb_result is not None)
        if not pb_result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )
        payload, pb_token = pb_result
        response.set_cookie(value=pb_token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())
        logger.info(f"User '{payload.username}' logged in via PocketBase (role={payload.role!r})")
        return {
            "ok": True,
            "user_id": payload.user_id,
            "username": payload.username,
            "role": payload.role,
            "is_admin": payload.role == "admin",
        }

    # Standard JWT + bcrypt mode
    result = authenticate(body.username, body.password)
    _record_login_result(body.username, client_ip, success=result is not None)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_token(result.username, result.role, result.user_id)
    response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())

    logger.info(f"User '{result.username}' logged in (role={result.role!r})")
    return {
        "ok": True,
        "user_id": result.user_id,
        "username": result.username,
        "role": result.role,
        "is_admin": result.role == "admin",
    }


_PASSWORD_RESET_PUBLIC_RESPONSE = {
    "ok": True,
    "message": "If this address has an account, a password reset code has been sent.",
}


@router.post("/password-reset/request-code", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset_code(
    body: PasswordResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Issue a reset code without revealing whether the account exists."""
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — password reset is not available.",
        )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the configured identity provider to reset this password.",
        )
    try:
        challenge = password_reset.issue_challenge(body.email, _client_ip(request))
        info = get_user_info(body.email)
        deliverable = bool(
            password_reset.email_delivery_configured()
            and info
            and not bool(info.get("disabled", False))
            and bool(info.get("email_verified", True))
        )
        # Always enqueue the same background callable.  The HTTP response is
        # therefore independent of account existence and SMTP latency/failure.
        background_tasks.add_task(
            password_reset.deliver_password_reset_challenge,
            challenge,
            deliverable=deliverable,
        )
    except password_reset.PasswordResetRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another password reset code.",
            headers={"Retry-After": "60"},
        ) from exc
    except password_reset.PasswordResetError as exc:
        logger.warning("Password reset challenge service unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset is temporarily unavailable.",
        ) from exc
    return dict(_PASSWORD_RESET_PUBLIC_RESPONSE)


@router.post("/password-reset/confirm")
async def confirm_password_reset(body: ConfirmPasswordResetRequest) -> dict:
    """Consume a one-time code, replace the password, and revoke old JWTs."""
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — password reset is not available.",
        )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the configured identity provider to reset this password.",
        )
    try:
        valid = password_reset.consume_challenge(body.email, body.code)
    except password_reset.PasswordResetError as exc:
        logger.warning("Password reset verification unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset is temporarily unavailable.",
        ) from exc
    if not valid or not reset_password(body.email, body.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset code.",
        )
    try:
        login_rate_limit.clear_login_history(body.email)
    except login_rate_limit.LoginRateLimitUnavailable:
        # The password/version update is already committed. Old tokens are
        # revoked even if cleanup of failed-login history needs later repair.
        logger.exception("Could not clear login history after password reset")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the JWT cookie.

    Deletion attributes mirror ``login`` structurally via ``_cookie_attrs()``
    (see the rationale there and #623).
    """
    response.delete_cookie(**_cookie_attrs())
    return {"ok": True}


@router.post("/register/request-code", status_code=status.HTTP_202_ACCEPTED)
async def request_registration_code(
    body: EmailRegistrationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Request a one-time code without revealing whether an account exists."""
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — registration is not available.",
        )
    auth_settings = load_auth_settings()
    if not bool(auth_settings.get("self_registration_enabled", True)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is currently unavailable.",
        )
    if not bool(auth_settings.get("email_verification_required", True)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is required for public registration.",
        )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email registration is not available in PocketBase mode.",
        )
    email = body.email
    client_ip = request.client.host if request.client else "unknown"
    try:
        # Do the cheap persistent limiter check before bcrypt, which is
        # intentionally expensive and must not become an unauthenticated DoS
        # primitive.
        email_verification.check_registration_rate_limit(email, client_ip)
        password_hash = hash_password(body.password)
        challenge = email_verification.issue_challenge(email, password_hash, client_ip)
        deliverable = bool(
            email_verification.email_delivery_configured() and get_user_info(email) is None
        )
        # Existing and new accounts take the same bcrypt/ledger path and queue
        # the same background callable. SMTP latency or failure cannot turn
        # the endpoint into an account-existence oracle.
        background_tasks.add_task(
            email_verification.deliver_registration_challenge,
            challenge,
            deliverable=deliverable,
        )
    except (
        email_verification.VerificationRateLimited,
        email_verification.VerificationCooldown,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification code.",
            headers={"Retry-After": "60"},
        ) from exc
    except email_verification.EmailVerificationError as exc:
        logger.warning("Registration verification service unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is temporarily unavailable.",
        ) from exc

    return {
        "ok": True,
        "verification_required": True,
        "message": "If this address can register, a verification code has been sent.",
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: VerifyRegistrationRequest, response: Response) -> dict:
    """Consume a code and create the verified account exactly once."""
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — registration is not available.",
        )
    auth_settings = load_auth_settings()
    if not bool(auth_settings.get("self_registration_enabled", True)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is currently unavailable.",
        )
    if not bool(auth_settings.get("email_verification_required", True)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is required for public registration.",
        )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email registration is not available in PocketBase mode.",
        )
    password_hash = email_verification.consume_challenge(body.email, body.code)
    if not password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    created, record = add_verified_user(body.email, password_hash)
    if not created:
        # The challenge is already consumed; never overwrite an existing
        # password when two verification requests race.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already registered.",
        )

    username = body.email
    role = str(record.get("role") or "user")
    user_id = str(record.get("id") or "")
    trial_pending = await _provision_commercial_trial(user_id, role)
    token = create_token(username, role, user_id)
    response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())
    logger.info(
        "Verified account created for email domain=%s role=%s",
        username.rsplit("@", 1)[-1],
        role,
    )
    return {
        "ok": True,
        "user_id": user_id,
        "username": username,
        "role": role,
        "is_first_user": role == "admin",
        "is_admin": role == "admin",
        "trial_pending": trial_pending,
    }


@router.get("/is_first_user")
async def check_is_first_user() -> dict:
    """Legacy compatibility endpoint without exposing deployment state."""
    # The registration UI no longer calls this endpoint. Keeping a constant
    # response avoids breaking older clients while preventing public account
    # enumeration of whether an admin has already registered.
    return {"is_first_user": False}


# ---------------------------------------------------------------------------
# Profile endpoints (any authenticated user, self-service)
# ---------------------------------------------------------------------------

_AVATAR_MAX_BYTES = 1 * 1024 * 1024
_AVATAR_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _sniff_image(data: bytes) -> str | None:
    """Detect a supported raster image format from its magic bytes.

    The uploaded filename and Content-Type are attacker-controlled, so the
    stored extension (and the media type served back) is derived from the
    bytes alone. SVG is deliberately unsupported — serving user-supplied SVG
    is a stored-XSS vector.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _require_profile_identity(payload: TokenPayload | None) -> TokenPayload:
    """Shared guard for the self-service profile endpoints."""
    if not AUTH_ENABLED or payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — profiles are not available.",
        )
    return payload


@router.get("/profile", response_model=UserInfo)
async def get_profile(
    payload: TokenPayload | None = Depends(require_auth),
) -> UserInfo:
    """Return the current user's own account info."""
    current = _require_profile_identity(payload)
    info = get_user_info(current.username)
    if info is None:
        # PocketBase-backed identities have no local record; fall back to the
        # token claims so the profile page still renders.
        return UserInfo(
            id=current.user_id,
            username=current.username,
            role=current.role,
            created_at="",
        )
    return UserInfo(**info)


@router.get("/profile/export")
async def export_profile_data(
    payload: TokenPayload | None = Depends(require_auth),
) -> FileResponse:
    """Download a privacy-safe ZIP snapshot of the current local account."""
    current = _require_profile_identity(payload)
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account export is managed by the configured identity provider.",
        )
    try:
        commercial_data = await _commercial_export_data(current.user_id)
        result = await asyncio.to_thread(
            account_lifecycle.export_account,
            current.username,
            actor_id=current.user_id,
            commercial_data=commercial_data,
        )
    except account_lifecycle.AccountNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    except (account_lifecycle.AccountLifecycleError, RuntimeError) as exc:
        logger.exception("Account export failed for user id %s", current.user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account export could not be created.",
        ) from exc
    return FileResponse(
        path=str(result.path),
        media_type="application/zip",
        filename="deeptutor-account-export.zip",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(result.path.unlink, missing_ok=True),
    )


@router.delete("/profile")
async def delete_own_account(
    body: DeleteOwnAccountRequest,
    request: Request,
    response: Response,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Irreversibly erase the current regular account after password proof."""
    current = _require_profile_identity(payload)
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account deletion is managed by the configured identity provider.",
        )
    if current.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Demote the administrator before deleting the account.",
        )

    client_ip = _client_ip(request)
    _check_login_rate_limit(current.username, client_ip)
    confirmed = authenticate(current.username, body.password)
    _record_login_result(current.username, client_ip, success=confirmed is not None)
    if confirmed is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )
    try:
        result = await _delete_account_saga(
            current.username,
            expected_user_id=current.user_id,
            actor_id=current.user_id,
            actor_username=current.username,
        )
    except account_lifecycle.AccountLifecycleForbidden as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except account_lifecycle.AccountLifecycleError as exc:
        logger.exception("Self-service account deletion failed for user id %s", current.user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account deletion could not be completed. The account is disabled.",
        ) from exc
    response.delete_cookie(**_cookie_attrs())
    return {"ok": result.deleted, "already_deleted": result.already_deleted}


@router.put("/profile")
async def update_profile(
    body: UpdateProfileRequest,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Update the current user's own avatar marker (icon choice or reset).

    Only the validated ``icon:<name>:<color>`` form (or empty string) is
    accepted here; ``img:`` markers are owned by the upload endpoint.
    """
    current = _require_profile_identity(payload)
    if not set_avatar(current.username, body.avatar):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # The marker no longer references an uploaded image, so drop the file.
    from deeptutor.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    return {"ok": True, "avatar": body.avatar}


@router.put("/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Upload an avatar image for the current user.

    The client is expected to crop/resize before uploading; the server only
    enforces a size cap and validates the format by magic bytes. Not available
    in PocketBase mode (those identities have no local user record).
    """
    current = _require_profile_identity(payload)
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar upload is not available in PocketBase mode.",
        )
    if not current.user_id or not _USER_ID_RE.match(current.user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot store an avatar for this account.",
        )
    info = get_user_info(current.username)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = await file.read(_AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Avatar image is too large (max 1 MB).",
        )
    ext = _sniff_image(data)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Avatar must be a PNG, JPEG or WebP image.",
        )

    from deeptutor.multi_user.identity import save_avatar_file

    # Bump the version embedded in the marker so clients cache-bust the URL.
    previous = str(info.get("avatar") or "")
    version = 1
    if previous.startswith("img:"):
        try:
            version = int(previous.split(":", 1)[1]) + 1
        except ValueError:
            version = 1
    marker = f"img:{version}"

    save_avatar_file(current.user_id, data, ext)
    if not set_avatar(current.username, marker):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    logger.info(f"User '{current.username}' uploaded a new avatar ({ext}, {len(data)} bytes)")
    return {"ok": True, "avatar": marker}


@router.delete("/profile/avatar")
async def remove_avatar(
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Remove the current user's uploaded avatar image and reset the marker."""
    current = _require_profile_identity(payload)
    from deeptutor.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    set_avatar(current.username, "")
    return {"ok": True, "avatar": ""}


@router.get("/avatar/{user_id}")
async def get_avatar_image(
    user_id: str,
    _: TokenPayload | None = Depends(require_auth),
) -> FileResponse:
    """Serve a stored avatar image. Any authenticated user may view avatars
    (they appear in the admin table and next to the viewer's own profile)."""
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    from deeptutor.multi_user.identity import get_avatar_file

    target = get_avatar_file(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    media_type = _AVATAR_MEDIA_TYPES.get(target.suffix.lstrip("."), "application/octet-stream")
    headers = {
        # Private user content; the marker version in the URL handles busting.
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }
    return FileResponse(path=str(target), media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Admin-only endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserInfo])
async def get_users(_: TokenPayload = Depends(require_admin)) -> list[UserInfo]:
    """List all registered users. Requires admin role."""
    return [UserInfo(**u) for u in list_users()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def admin_create_user(
    body: RegisterRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Admin-only: create a new user account.

    Replaces the public ``/register`` flow once the first admin exists. The
    new account is always created with role=``user``; admins can promote
    later via ``PUT /users/{username}/role``.
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — user creation is not available.",
        )

    if POCKETBASE_ENABLED:
        result = register_pb(username=body.username, email=body.username, password=body.password)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Failed to create user — username may already be taken.",
            )
        logger.info(
            f"Admin '{current.username if current else 'local'}' created PocketBase user "
            f"'{body.username}'"
        )
        user_id = str(result.get("id") or "")
        trial_pending = await _provision_commercial_trial(user_id, "user")
        return {
            "ok": True,
            "user_id": user_id,
            "username": body.username,
            "role": "user",
            "is_admin": False,
            "trial_pending": trial_pending,
        }

    existing = {u["username"] for u in list_users()}
    if body.username in existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    add_user(body.username, body.password)
    user_id = ""
    role = "user"
    for item in list_users():
        if item.get("username") == body.username:
            user_id = str(item.get("id") or "")
            role = str(item.get("role") or "user")
            break
    logger.info(
        f"Admin '{current.username if current else 'local'}' created user '{body.username}' "
        f"(role={role!r})"
    )
    trial_pending = await _provision_commercial_trial(user_id, role)
    return {
        "ok": True,
        "user_id": user_id,
        "username": body.username,
        "role": role,
        "is_admin": role == "admin",
        "trial_pending": trial_pending,
    }


@router.delete("/users/{username}", status_code=status.HTTP_200_OK)
async def remove_user(
    username: str,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Delete a user. Admins cannot delete their own account."""
    if current and username == current.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account deletion is managed by the configured identity provider.",
        )
    canonical_username = username.strip().casefold() if "@" in username else username.strip()
    target_info = get_user_info(canonical_username)
    expected_user_id = str(target_info.get("id") or "") if target_info else None
    try:
        result = await _delete_account_saga(
            canonical_username,
            expected_user_id=expected_user_id,
            actor_id=current.user_id,
            actor_username=current.username,
        )
    except account_lifecycle.AccountNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    except account_lifecycle.AccountLifecycleForbidden as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except account_lifecycle.AccountLifecycleError as exc:
        logger.exception("Admin account deletion failed actor=%s", current.user_id or "unknown")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account deletion could not be completed. The account is disabled.",
        ) from exc

    logger.info(
        "Admin '%s' deleted account (already_deleted=%s)",
        current.username,
        result.already_deleted,
    )
    return {"ok": result.deleted, "already_deleted": result.already_deleted}


@router.put("/users/{username}/role", status_code=status.HTTP_200_OK)
async def update_user_role(
    username: str,
    body: SetRoleRequest,
    current: TokenPayload = Depends(require_admin),
) -> dict:
    """Change a user's role. Admins cannot change their own role."""
    if current and username == current.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )

    updated = set_role(username, body.role)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    logger.info(
        f"Admin '{current.username if current else 'local'}' set '{username}' role to {body.role!r}"
    )
    return {"ok": True, "username": username, "role": body.role}
