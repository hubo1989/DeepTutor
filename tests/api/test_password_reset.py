from __future__ import annotations

from types import SimpleNamespace

from fastapi import BackgroundTasks, HTTPException
import pytest

from deeptutor.api.routers import auth as auth_router


def _request(ip: str = "127.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


async def _request_code(email: str):
    tasks = BackgroundTasks()
    result = await auth_router.request_password_reset_code(
        auth_router.PasswordResetRequest(email=email), _request(), tasks
    )
    await tasks()
    return result


@pytest.fixture
def password_reset_env(auth_isolated_root, monkeypatch):
    from deeptutor.services import auth as auth_service
    from deeptutor.services import password_reset

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-auth-secret")
    monkeypatch.setattr(password_reset, "email_delivery_configured", lambda: True)
    sent = []
    monkeypatch.setattr(password_reset, "send_password_reset_email", sent.append)
    auth_service.add_user("user@example.com", "old-password", role="user")
    return sent


@pytest.mark.asyncio
async def test_password_reset_is_one_time_and_revokes_existing_tokens(
    password_reset_env,
) -> None:
    from deeptutor.services import auth as auth_service

    old_login = auth_service.authenticate("user@example.com", "old-password")
    assert old_login is not None
    old_token = auth_service.create_token(old_login.username, old_login.role, old_login.user_id)

    requested = await _request_code("USER@example.com")
    assert requested["ok"] is True
    challenge = password_reset_env[0]

    with pytest.raises(HTTPException) as invalid:
        await auth_router.confirm_password_reset(
            auth_router.ConfirmPasswordResetRequest(
                email="user@example.com",
                code="000000",
                new_password="new-password",
            )
        )
    assert invalid.value.status_code == 400

    reset = await auth_router.confirm_password_reset(
        auth_router.ConfirmPasswordResetRequest(
            email="user@example.com",
            code=challenge.code,
            new_password="new-password",
        )
    )
    assert reset == {"ok": True}
    assert auth_service.authenticate("user@example.com", "old-password") is None
    assert auth_service.authenticate("user@example.com", "new-password") is not None
    assert auth_service.decode_token(old_token) is None

    with pytest.raises(HTTPException) as replay:
        await auth_router.confirm_password_reset(
            auth_router.ConfirmPasswordResetRequest(
                email="user@example.com",
                code=challenge.code,
                new_password="another-password",
            )
        )
    assert replay.value.status_code == 400


@pytest.mark.asyncio
async def test_password_reset_request_does_not_enumerate_accounts(
    password_reset_env,
) -> None:
    existing = await _request_code("user@example.com")
    missing = await _request_code("missing@example.com")

    assert existing == missing
    assert len(password_reset_env) == 1


@pytest.mark.asyncio
async def test_password_reset_delivery_failure_does_not_change_public_response(
    password_reset_env, monkeypatch
) -> None:
    from deeptutor.services import password_reset

    def fail_delivery(_challenge) -> None:
        raise password_reset.PasswordResetUnavailable("smtp unavailable")

    monkeypatch.setattr(password_reset, "send_password_reset_email", fail_delivery)
    tasks = BackgroundTasks()
    response = await auth_router.request_password_reset_code(
        auth_router.PasswordResetRequest(email="user@example.com"),
        _request("127.0.0.5"),
        tasks,
    )

    assert response == auth_router._PASSWORD_RESET_PUBLIC_RESPONSE
    await tasks()  # the background helper contains and logs SMTP failure


@pytest.mark.asyncio
async def test_password_reset_is_unavailable_when_auth_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", False)

    with pytest.raises(HTTPException) as exc:
        await auth_router.request_password_reset_code(
            auth_router.PasswordResetRequest(email="user@example.com"),
            _request(),
            BackgroundTasks(),
        )

    assert exc.value.status_code == 400
