"""Registration leaves grant creation to the first admin save (upstream model).

Upstream's grants module has no create-time initializer: ``load_grant`` falls
back to ``empty_grant``, which resolves the deployment's default quotas at read
time. The v1.6.15 merge dropped the fork's ``initialize_grant`` but kept its
callers in ``services.auth``, so every account creation raised ImportError into
a swallowed ``except Exception``. These tests pin the upstream-aligned
behavior that replaces it.
"""

from __future__ import annotations


def test_verified_registration_creates_no_grant_file(mu_isolated_root, monkeypatch):
    from deeptutor.multi_user.grants import grant_path
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    created, record = auth_service.add_verified_user(
        "newbie@example.com", "hashed-password"
    )

    assert created is True
    # Upstream semantics: no snapshot at creation time…
    assert not grant_path(record["id"]).exists()


def test_missing_grant_falls_back_to_default_quotas(mu_isolated_root, monkeypatch):
    """…and the empty-grant fallback still enforces the deployment defaults."""
    from deeptutor.multi_user.grants import load_grant
    from deeptutor.multi_user.token_quota import default_quota, quota_policy_from_grant
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    _created, record = auth_service.add_verified_user(
        "newbie@example.com", "hashed-password"
    )

    grant = load_grant(record["id"])
    assert grant["models"]["llm"] == []
    expected = default_quota()
    assert grant["quota"]["llm"] == expected["llm"]

    policy = quota_policy_from_grant(grant)
    assert policy.daily_tokens == expected["llm"]["daily_tokens"]
    assert policy.monthly_tokens == expected["llm"]["monthly_tokens"]


def test_admin_save_creates_the_grant_file(mu_isolated_root, seed_user):
    """The admin grant endpoint remains the single create path."""
    from deeptutor.multi_user import grants

    record = seed_user("alice", role="user")
    user_id = record["id"]
    assert not grants.grant_path(user_id).exists()

    grants.save_grant(
        user_id,
        {
            "models": {"llm": [{"profile_id": "p1", "model_ids": ["m1"]}]},
            "quota": {"llm": {"daily_tokens": 7, "monthly_tokens": 70}},
        },
    )
    assert grants.grant_path(user_id).exists()
    assert grants.load_grant(user_id)["quota"]["llm"]["daily_tokens"] == 7


def test_admin_save_user_path_creates_no_grant_either(mu_isolated_root, monkeypatch):
    """``save_user`` (admin-created accounts) follows the same no-snapshot rule."""
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.grants import grant_path
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    auth_service.add_user("bob", "plain-password", role="user")

    record = identity.get_user("bob")
    assert record is not None
    assert not grant_path(record["id"]).exists()
