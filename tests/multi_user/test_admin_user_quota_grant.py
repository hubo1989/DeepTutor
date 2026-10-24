"""Per-user quota grants through the admin grants API.

The grants PUT endpoint is the only per-user quota write path: ``quota`` in the
payload wins over the deployment default (``normalize_quotas`` keeps explicit
blocks), while an absent block falls back to it. These tests pin both halves of
that contract plus the enforcement read path, because the admin UI relies on
exactly this behavior to edit a user's limits without touching anything else.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import multi_user
from deeptutor.api.routers.auth import require_admin


@pytest.fixture
def admin_client(mu_isolated_root, seed_user):
    seed_user("alice", role="user")
    app = FastAPI()
    app.include_router(multi_user.router, prefix="/api/multi-user")
    app.dependency_overrides[require_admin] = lambda: None
    return TestClient(app)


def _grants_url(user_id: str) -> str:
    return f"/api/multi-user/users/{user_id}/grants"


def _user_id(client: TestClient, username: str) -> str:
    listing = client.get("/api/multi-user/users")
    assert listing.status_code == 200
    match = next(
        (item for item in listing.json().get("users", []) if item.get("username") == username),
        None,
    )
    assert match, f"user {username} not listed"
    return str(match["id"])


def test_put_with_explicit_quota_overrides_default(admin_client):
    user_id = _user_id(admin_client, "alice")
    response = admin_client.put(
        _grants_url(user_id),
        json={
            "grant": {
                "version": 3,
                "user_id": user_id,
                "models": {"llm": []},
                "quota": {
                    "llm": {"daily_tokens": 500_000, "monthly_tokens": 9_000_000},
                    "embedding": {"daily_tokens": 2_000_000, "monthly_tokens": 20_000_000},
                    "mineru": {"daily_pages": 7, "monthly_pages": 70, "max_pages_per_file": 6},
                },
            }
        },
    )
    assert response.status_code == 200, response.text
    grant = response.json()["grant"]

    # The explicit block wins over the deployment default on every resource.
    assert grant["quota"]["llm"] == {"daily_tokens": 500_000, "monthly_tokens": 9_000_000}
    assert grant["quota"]["mineru"]["daily_pages"] == 7
    # The legacy token_quota alias must stay in sync with quota.llm so older
    # readers and the usage gates keep seeing the same limit.
    assert grant["token_quota"] == grant["quota"]["llm"]

    # Round-trip: a later GET (what the admin UI loads) returns the same quota.
    fetched = admin_client.get(_grants_url(user_id)).json()["grant"]
    assert fetched["quota"] == grant["quota"]

    # Enforcement read path: the limiter consumes exactly the granted values.
    from deeptutor.multi_user.grants import load_grant
    from deeptutor.multi_user.token_quota import quota_policy_from_grant

    policy = quota_policy_from_grant(load_grant(user_id))
    assert policy.daily_tokens == 500_000
    assert policy.monthly_tokens == 9_000_000


def test_put_without_quota_falls_back_to_default(admin_client, monkeypatch, tmp_path):
    settings_file = tmp_path / "auth.json"
    settings_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "default_quota": {"llm": {"daily_tokens": 123, "monthly_tokens": 456}},
            }
        ),
        encoding="utf-8",
    )
    from deeptutor.services import config as services_config

    def _load_settings():
        return json.loads(settings_file.read_text(encoding="utf-8"))

    monkeypatch.setattr(services_config, "load_auth_settings", _load_settings)

    user_id = _user_id(admin_client, "alice")
    response = admin_client.put(
        _grants_url(user_id),
        json={"grant": {"version": 3, "user_id": user_id, "models": {"llm": []}}},
    )
    assert response.status_code == 200, response.text
    grant = response.json()["grant"]
    assert grant["quota"]["llm"]["daily_tokens"] == 123
    assert grant["quota"]["llm"]["monthly_tokens"] == 456


def test_admin_targets_are_rejected(admin_client, seed_user):
    seed_user("root", role="admin")
    user_id = _user_id(admin_client, "root")
    response = admin_client.put(
        _grants_url(user_id),
        json={"grant": {"version": 3, "user_id": user_id, "models": {"llm": []}}},
    )
    assert response.status_code == 403


def test_unknown_quota_fields_are_sanitized_not_stored(admin_client):
    """normalize_quotas drops unknown fields, so a stray key cannot smuggle
    arbitrary data into the persisted grant while valid limits still apply."""
    user_id = _user_id(admin_client, "alice")
    response = admin_client.put(
        _grants_url(user_id),
        json={
            "grant": {
                "version": 3,
                "user_id": user_id,
                "models": {"llm": []},
                "quota": {"llm": {"daily_tokens": 1, "api_key": "sk"}},
            }
        },
    )
    assert response.status_code == 200, response.text
    grant = response.json()["grant"]
    assert grant["quota"]["llm"] == {
        "daily_tokens": 1,
        "monthly_tokens": grant["quota"]["llm"]["monthly_tokens"],
    }
    assert "api_key" not in grant["quota"]["llm"]
