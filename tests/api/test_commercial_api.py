from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.commercial.access import CommercialAccess, require_commercial_access
from deeptutor.commercial.api import router
from deeptutor.commercial.models import ResolvedEntitlements, SubscriptionStatus
from deeptutor.commercial.runtime import CommercialSettings
from deeptutor.multi_user.context import set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.auth import TokenPayload


def test_commercial_me_returns_sanitized_current_plan(tmp_path, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    user = CurrentUser(
        id="u_me",
        username="me@example.com",
        role="user",
        scope=UserScope(kind="user", user_id="u_me", root=tmp_path),
    )
    resolved = ResolvedEntitlements(
        customer_id="cus_secret",
        subscription_id="sub_secret",
        plan_version_id="plan_trial_v1",
        status=SubscriptionStatus.TRIALING,
        active=True,
        resolved_at=now,
        valid_until=now + timedelta(days=7),
        values={
            "models.llm": {"profile_id": "trial-profile", "model_ids": ["model-a"]},
            "quota.llm_tokens": {"limit": 100_000, "window": "utc_day"},
        },
    )

    class Runtime:
        settings = CommercialSettings(
            enabled=True,
            database_url="postgresql://unused/test",
            trial_model_binding=None,
        )

    monkeypatch.setattr("deeptutor.commercial.api.get_commercial_runtime", lambda: Runtime())

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    access_state = [resolved]

    async def install_access():
        set_current_user(user)
        from deeptutor.commercial.access import set_commercial_access

        set_commercial_access(CommercialAccess(user.id, access_state[0]))
        return TokenPayload(username=user.username, role="user", user_id=user.id)

    app.dependency_overrides[require_commercial_access] = install_access
    response = TestClient(app).get("/api/v1/commercial/me")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "trialing"
    assert body["plan_version_id"] == "plan_trial_v1"
    assert body["values"]["quota.llm_tokens"]["limit"] == 100_000
    serialized = response.text
    assert "cus_secret" not in serialized
    assert "sub_secret" not in serialized

    access_state[0] = replace(
        resolved,
        status=SubscriptionStatus.EXPIRED,
        active=False,
        valid_until=now - timedelta(seconds=1),
    )
    expired_response = TestClient(app).get("/api/v1/commercial/me")
    assert expired_response.status_code == 200
    assert expired_response.json()["status"] == "expired"


def test_commercial_me_admin_bypass(tmp_path, monkeypatch) -> None:
    admin = CurrentUser(
        id="u_admin",
        username="admin@example.com",
        role="admin",
        scope=UserScope(kind="admin", user_id="u_admin", root=tmp_path),
    )

    class Runtime:
        settings = CommercialSettings(
            enabled=True,
            database_url="postgresql://unused/test",
            trial_model_binding=None,
        )

    monkeypatch.setattr("deeptutor.commercial.api.get_commercial_runtime", lambda: Runtime())
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def install_admin():
        set_current_user(admin)
        return TokenPayload(username=admin.username, role="admin", user_id=admin.id)

    app.dependency_overrides[require_commercial_access] = install_admin
    response = TestClient(app).get("/api/v1/commercial/me")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "is_admin": True,
        "status": "admin",
        "valid_until": None,
        "plan_version_id": None,
        "values": {},
    }
