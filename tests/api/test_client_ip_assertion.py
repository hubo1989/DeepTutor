"""Route-level tests for the trusted-proxy client-IP assertion.

Through the bundled Next frontend proxy every visitor arrives at the backend
from the proxy's loopback address, so without an assertion all visitors would
share one rate-limit bucket and a single visitor could lock out the whole
deployment. The proxy therefore re-asserts the address it observed on its own
TCP socket via ``x-deeptutor-client-ip`` (see web/lib/connection-stamp.ts and
web/lib/backend-forward.ts), and ``_client_ip`` honors that assertion only when
the ASGI peer is an explicitly trusted proxy address.

These tests exercise the real router through an ASGI wrapper that rewrites the
peer address, the same pattern as test_session_handoff.py's loopback proxy:

1. a trusted proxy's assertions isolate visitors into separate limiter
   buckets, so different asserted IPs do not lock each other out;
2. an untrusted direct caller's forged assertion header is ignored — its own
   peer address is the rate-limit identity;
3. a trusted proxy without an assertion degrades to the shared peer bucket.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth as auth_router
from deeptutor.services import auth as auth_service
from deeptutor.services import login_rate_limit


def _proxy_asgi(app, peer: str, asserted_ip: str | None):
    """Wrap an ASGI app: fixed peer address plus an optional assertion header."""

    async def forwarded(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer, 12345)}
            if asserted_ip is None:
                scope["headers"] = [
                    (name, value)
                    for (name, value) in scope.get("headers", [])
                    if name != b"x-deeptutor-client-ip"
                ]
            else:
                scope["headers"] = [
                    (name, value)
                    for (name, value) in scope.get("headers", [])
                    if name != b"x-deeptutor-client-ip"
                ] + [(b"x-deeptutor-client-ip", asserted_ip.encode())]
        await app(scope, receive, send)

    return forwarded


@pytest.fixture
def rate_limited_app(tmp_path: Path, monkeypatch):
    """A minimal app with auth on, an existing user, and a tiny IP bucket."""
    from deeptutor.multi_user import identity

    system_root = tmp_path / "data" / "system"
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    state_root = tmp_path / "state"
    monkeypatch.setattr(
        login_rate_limit, "_db_path", lambda: state_root / "login_rate_limit.sqlite3"
    )
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    # Keep the trusted-proxy default (loopback only); tests rely on it.
    monkeypatch.setattr(auth_router, "TRUSTED_FRONTEND_PROXY_IPS", frozenset({"127.0.0.1", "::1"}))

    auth_service.add_user("alice", "alice-password-123", role="user")

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return app


def _fail_login(client: TestClient) -> int:
    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    return response.status_code


def test_trusted_proxy_assertions_isolate_visitors(rate_limited_app):
    """Different asserted IPs through the proxy do not share a bucket."""
    proxy = _proxy_asgi(rate_limited_app, "127.0.0.1", "203.0.113.9")
    with TestClient(proxy) as visitor_a:
        for _ in range(3):
            assert _fail_login(visitor_a) == 401

    # A different visitor, still through the same trusted proxy, starts fresh.
    other = _proxy_asgi(rate_limited_app, "127.0.0.1", "198.51.100.77")
    with TestClient(other) as visitor_b:
        status = _fail_login(visitor_b)
    assert status == 401, "a distinct asserted IP must not inherit the lockout"


def test_untrusted_peer_forged_assertion_is_ignored(rate_limited_app, monkeypatch):
    """A direct (untrusted) caller cannot pick its rate-limit identity."""
    monkeypatch.setattr(login_rate_limit, "_policy", lambda: (3, 3, 3, 60, 60))
    # Warm the attacker's real peer bucket to its limit with failures, each
    # carrying a DIFFERENT forged assertion so a trusted assertion would have
    # split them across buckets and never locked anything.
    for index in range(3):
        rotating = _proxy_asgi(rate_limited_app, "203.0.113.9", f"198.51.100.{index}")
        with TestClient(rotating) as attacker:
            assert _fail_login(attacker) == 401
    # The next attempt from the same peer must be locked out: the forged
    # headers changed nothing about the identity the limiter used.
    final = _proxy_asgi(rate_limited_app, "203.0.113.9", "198.51.100.99")
    with TestClient(final) as locked_out:
        assert _fail_login(locked_out) == 429


def test_trusted_proxy_without_assertion_falls_back_to_peer(rate_limited_app):
    """No assertion header → the proxy's peer address is the identity.

    This is the boot-race / degraded-mode path: assertions are absent, so all
    visitors share the proxy bucket (pre-existing behavior, no worse).
    """
    unasserted = _proxy_asgi(rate_limited_app, "127.0.0.1", None)
    with TestClient(unasserted) as visitor:
        assert _fail_login(visitor) == 401

    # And a direct untrusted call sees its own address too.
    direct = _proxy_asgi(rate_limited_app, "203.0.113.9", None)
    with TestClient(direct) as caller:
        assert _fail_login(caller) == 401
