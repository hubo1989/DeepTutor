"""Authorization gates shared by legacy WebSocket entry points."""

from __future__ import annotations

from fastapi import HTTPException, WebSocketDisconnect
import pytest

from deeptutor.services.auth import TokenPayload


class _FakeWebSocket:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.query_params = {"token": "valid-token"}
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self._messages = list(messages or [])
        self.accepted = False
        self.close_codes: list[int] = []
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        if self._messages:
            return self._messages.pop(0)
        raise WebSocketDisconnect()

    async def receive_text(self) -> str:
        if self._messages:
            import json

            return json.dumps(self._messages.pop(0))
        raise WebSocketDisconnect()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def send_text(self, payload: str) -> None:
        import json

        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del reason
        self.close_codes.append(code)


def _patch_valid_handshake(monkeypatch) -> None:
    from deeptutor.api.routers import auth

    payload = TokenPayload(username="alice", role="user", user_id="u_alice")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["chat", "question_mimic", "question_generate", "quiz_judge", "book", "partner"],
)
async def test_consuming_legacy_websockets_reject_user_without_llm_grant(
    endpoint, monkeypatch
) -> None:
    from deeptutor.api.routers import book, chat, partners, question, quiz_judge
    from deeptutor.multi_user import model_access

    _patch_valid_handshake(monkeypatch)
    monkeypatch.setattr(model_access, "has_capability_access", lambda _capability: False)

    async def missing_partner(_partner_id: str):
        raise HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(partners, "get_partner_manager", lambda: object())
    monkeypatch.setattr(partners, "_ensure_running_partner", missing_partner)
    monkeypatch.setattr(book, "get_book_engine", lambda: object())

    ws = _FakeWebSocket()
    if endpoint == "chat":
        await chat.websocket_chat(ws)  # type: ignore[arg-type]
    elif endpoint == "question_mimic":
        await question.websocket_mimic_generate(ws)  # type: ignore[arg-type]
    elif endpoint == "question_generate":
        await question.websocket_question_generate(ws)  # type: ignore[arg-type]
    elif endpoint == "quiz_judge":
        await quiz_judge.websocket_quiz_judge(ws)  # type: ignore[arg-type]
    elif endpoint == "book":
        await book.book_websocket(ws)  # type: ignore[arg-type]
    else:
        await partners.partner_chat_ws(ws, "partner-1")  # type: ignore[arg-type]

    assert ws.accepted is False
    assert ws.close_codes == [4003]


@pytest.mark.asyncio
async def test_legacy_chat_revalidates_identity_before_each_message(monkeypatch) -> None:
    from deeptutor.api.routers import auth, chat
    from deeptutor.multi_user import model_access

    valid = TokenPayload(username="alice", role="user", user_id="u_alice")
    decoded = iter([valid, None])
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: next(decoded))
    monkeypatch.setattr(model_access, "has_capability_access", lambda _capability: True)

    ws = _FakeWebSocket([{"message": ""}])
    await chat.websocket_chat(ws)  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws.close_codes == [4001]
    assert ws.sent == []


@pytest.mark.asyncio
async def test_kb_progress_socket_rejects_unowned_kb_before_accept(monkeypatch) -> None:
    from deeptutor.api.routers import knowledge

    _patch_valid_handshake(monkeypatch)

    def deny(_kb_ref: str):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    monkeypatch.setattr(knowledge, "resolve_kb", deny)
    ws = _FakeWebSocket()
    await knowledge.websocket_progress(ws, "other-users-kb")  # type: ignore[arg-type]

    assert ws.accepted is False
    assert ws.close_codes == [4004]
