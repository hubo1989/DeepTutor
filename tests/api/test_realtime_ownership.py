"""Owner checks for process-global realtime channels."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import WebSocketDisconnect
import pytest

from deeptutor.services.auth import TokenPayload


class _FakeWebSocket:
    def __init__(self, message: dict[str, object], payload: TokenPayload) -> None:
        self.query_params = {"token": "test-token"}
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self._messages = [json.dumps({"protocol_version": "2.0", **message})]
        self.payload = payload
        self.sent: list[dict[str, object]] = []
        self.accepted = False
        self.close_codes: list[int] = []

        self.app = SimpleNamespace(state=SimpleNamespace())

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


class _OwnerScopedStore:
    def __init__(self, *, owner_id: str, turn_id: str, session_id: str) -> None:
        self.owner_id = owner_id
        self.turn_id = turn_id
        self.session_id = session_id

    async def get_turn(self, turn_id: str):  # noqa: ANN201
        if turn_id != self.turn_id:
            return None
        return {"id": self.turn_id, "session_id": self.session_id}

    async def get_session(self, session_id: str):  # noqa: ANN201
        from deeptutor.multi_user.context import get_current_user

        if session_id != self.session_id or get_current_user().id != self.owner_id:
            return None
        return {"id": self.session_id}


class _InputBus:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def submit_input(self, content: str) -> None:
        self.inputs.append(content)


async def _run_user_input(
    monkeypatch: pytest.MonkeyPatch,
    *,
    caller: TokenPayload,
    requested_session_id: str,
) -> tuple[_FakeWebSocket, _InputBus]:
    from deeptutor.api.routers import auth, unified_ws

    turn_id = "turn_owned_by_alice"
    owner_session_id = "session_owned_by_alice"
    store = _OwnerScopedStore(
        owner_id="u_alice",
        turn_id=turn_id,
        session_id=owner_session_id,
    )
    bus = _InputBus()

    async def refresh_commercial_access() -> None:
        return None

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: caller)
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.install_commercial_access_for_current_user",
        refresh_commercial_access,
    )

    # The v2 socket resolves its turn service through the application
    # container rather than the legacy module-level manager accessor.
    async def _submit_user_input(turn_id: str, content: str, command_id: str) -> bool:
        turn = await store.get_turn(turn_id)
        if turn is None:
            return False
        # The turn's own session must match the one this caller reached it
        # through; a mismatch means the turn belongs to another session.
        if str(turn.get("session_id") or "") != requested_session_id:
            return False
        session = await store.get_session(turn.get("session_id") or "")
        if session is None:
            return False
        bus.submit_input(content)
        return True

    async def _noop_start() -> None:
        return None

    monkeypatch.setattr(
        "deeptutor.app.container.get_application_container",
        lambda: SimpleNamespace(
            turns=SimpleNamespace(
                store=store,
                submit_user_input=_submit_user_input,
            ),
            start=_noop_start,
        ),
    )
    monkeypatch.setattr("deeptutor.runtime.stream_bus.get_bus", lambda _turn_id: bus)

    ws = _FakeWebSocket(
        {
            "type": "user_input",
            "turn_id": turn_id,
            "content": "private answer",
            "command_id": "cmd-1",
        },
        caller,
    )
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]
    return ws, bus


@pytest.mark.asyncio
async def test_user_input_rejects_another_owners_turn(monkeypatch) -> None:
    ws, bus = await _run_user_input(
        monkeypatch,
        caller=TokenPayload(username="mallory", role="user", user_id="u_mallory"),
        requested_session_id="session_owned_by_alice",
    )

    assert bus.inputs == []
    # The v2 protocol acknowledges the command and reports the refusal as
    # a turn-not-active reason rather than a bare error frame.
    assert ws.sent == [
        {
            "type": "command_ack",
            "command_id": "cmd-1",
            "command_type": "user_input",
            "accepted": False,
            "turn_id": "turn_owned_by_alice",
            "error_code": "turn_not_active",
            "message": "Turn is not active: turn_owned_by_alice",
            "protocol_version": "2.0",
        }
    ]


@pytest.mark.asyncio
async def test_user_input_accepts_the_turn_owner(monkeypatch) -> None:
    ws, bus = await _run_user_input(
        monkeypatch,
        caller=TokenPayload(username="alice", role="user", user_id="u_alice"),
        requested_session_id="session_owned_by_alice",
    )

    # The v2 protocol acknowledges an accepted user_input.
    assert ws.sent == [
        {
            "type": "command_ack",
            "command_id": "cmd-1",
            "command_type": "user_input",
            "accepted": True,
            "turn_id": "turn_owned_by_alice",
            "error_code": "",
            "message": "",
            "protocol_version": "2.0",
        }
    ]
    assert bus.inputs == ["private answer"]


@pytest.mark.asyncio
async def test_user_input_rejects_owner_with_mismatched_session(monkeypatch) -> None:
    ws, bus = await _run_user_input(
        monkeypatch,
        caller=TokenPayload(username="alice", role="user", user_id="u_alice"),
        requested_session_id="different_session",
    )

    assert bus.inputs == []
    # The v2 protocol acknowledges the command and reports the refusal as
    # a turn-not-active reason rather than a bare error frame.
    assert ws.sent == [
        {
            "type": "command_ack",
            "command_id": "cmd-1",
            "command_type": "user_input",
            "accepted": False,
            "turn_id": "turn_owned_by_alice",
            "error_code": "turn_not_active",
            "message": "Turn is not active: turn_owned_by_alice",
            "protocol_version": "2.0",
        }
    ]


@pytest.mark.asyncio
async def test_user_input_keeps_auth_disabled_local_mode_compatible(monkeypatch) -> None:
    from deeptutor.api.routers import auth, unified_ws

    store = _OwnerScopedStore(
        owner_id="local-admin",
        turn_id="turn_local",
        session_id="session_local",
    )
    bus = _InputBus()

    async def refresh_commercial_access() -> None:
        return None

    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.install_commercial_access_for_current_user",
        refresh_commercial_access,
    )

    # The v2 socket resolves its turn service through the application
    # container rather than the legacy module-level manager accessor.
    async def _submit_user_input(turn_id: str, content: str, command_id: str) -> bool:
        turn = await store.get_turn(turn_id)
        if turn is None:
            return False
        session = await store.get_session(turn.get("session_id") or "")
        if session is None:
            return False
        bus.submit_input(content)
        return True

    async def _noop_start() -> None:
        return None

    monkeypatch.setattr(
        "deeptutor.app.container.get_application_container",
        lambda: SimpleNamespace(
            turns=SimpleNamespace(
                store=store,
                submit_user_input=_submit_user_input,
            ),
            start=_noop_start,
        ),
    )
    monkeypatch.setattr("deeptutor.runtime.stream_bus.get_bus", lambda _turn_id: bus)

    ws = _FakeWebSocket(
        {
            "type": "user_input",
            "turn_id": "turn_local",
            "content": "local answer",
            "command_id": "cmd-local",
        },
        TokenPayload(username="ignored", role="user", user_id="ignored"),
    )
    await unified_ws.unified_websocket(ws)  # type: ignore[arg-type]

    assert ws.sent == [
        {
            "type": "command_ack",
            "command_id": "cmd-local",
            "command_type": "user_input",
            "accepted": True,
            "turn_id": "turn_local",
            "error_code": "",
            "message": "",
            "protocol_version": "2.0",
        }
    ]
    assert bus.inputs == ["local answer"]
