"""WebSocket sessions must revalidate their JWT before each inbound message."""

from __future__ import annotations

import json

from fastapi import WebSocketDisconnect
import pytest

from deeptutor.services.auth import TokenPayload


class _FakeWebSocket:
    def __init__(
        self,
        messages: list[dict],
        *,
        token: str = "test-token",
        origin: str | None = None,
    ) -> None:
        self.query_params = {"token": token}
        self.cookies: dict[str, str] = {}
        self.headers = {} if origin is None else {"origin": origin}
        self._messages = [json.dumps(message) for message in messages]
        self.accepted = False
        self.sent: list[dict] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        raise WebSocketDisconnect()

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


@pytest.mark.asyncio
async def test_connected_socket_closes_before_processing_message_when_token_is_revoked(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    results = iter([valid, None])  # handshake succeeds; per-message check fails
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: next(results))

    ws = _FakeWebSocket([{"type": "ping"}])
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws.close_codes == [4001]
    assert ws.sent == []  # revoked message was never dispatched (no pong)


@pytest.mark.asyncio
async def test_connected_socket_closes_when_current_role_or_identity_changes(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    handshake = TokenPayload(username="alice", role="user", user_id="u_alice")
    changed = TokenPayload(username="alice", role="admin", user_id="u_alice")
    results = iter([handshake, changed])
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: next(results))

    ws = _FakeWebSocket([{"type": "ping"}])
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.close_codes == [4001]
    assert ws.sent == []


@pytest.mark.asyncio
async def test_connected_socket_processes_message_after_successful_revalidation(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    calls = 0

    def decode(_token):
        nonlocal calls
        calls += 1
        return valid

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", decode)

    ws = _FakeWebSocket([{"type": "ping"}])
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert calls == 2  # handshake + one inbound-message validation
    assert ws.close_codes == []
    assert ws.sent == [{"type": "pong"}]


@pytest.mark.asyncio
async def test_connected_socket_refreshes_commercial_snapshot_for_each_frame(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    commercial_refreshes = 0

    async def refresh_commercial():
        nonlocal commercial_refreshes
        commercial_refreshes += 1

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: valid)
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.install_commercial_access_for_current_user",
        refresh_commercial,
    )

    ws = _FakeWebSocket([{"type": "ping"}])
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert commercial_refreshes == 2  # handshake + inbound frame
    assert ws.sent == [{"type": "pong"}]


@pytest.mark.asyncio
async def test_connected_socket_fails_closed_when_subscription_refresh_breaks(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    calls = 0

    async def refresh_commercial():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: valid)
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.install_commercial_access_for_current_user",
        refresh_commercial,
    )

    ws = _FakeWebSocket([{"type": "ping"}])
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws.close_codes == [1011]
    assert ws.sent == []


@pytest.mark.asyncio
async def test_cross_site_websocket_origin_is_rejected_before_accept(monkeypatch) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: valid)
    monkeypatch.setattr(
        auth,
        "_configured_websocket_origins",
        lambda: {"https://app.example.com"},
        raising=False,
    )

    ws = _FakeWebSocket([{"type": "ping"}], origin="https://evil.example")
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.accepted is False
    assert ws.close_codes == [4003]
    assert ws.sent == []


@pytest.mark.asyncio
async def test_authenticated_websocket_ignores_wildcard_origin_configuration(
    monkeypatch,
) -> None:
    from deeptutor.api.routers import auth, unified_ws

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: valid)
    monkeypatch.setattr(auth, "_configured_websocket_origins", lambda: {"*"})

    ws = _FakeWebSocket([{"type": "ping"}], origin="https://evil.example")
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.accepted is False
    assert ws.close_codes == [4003]
