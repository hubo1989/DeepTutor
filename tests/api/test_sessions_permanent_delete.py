from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import sessions as router
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.mark.asyncio
async def test_delete_cancels_active_turn_then_removes_messages(tmp_path, monkeypatch):
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    await store.create_session(session_id="root", title="root")
    await store.add_message("root", "user", "private conversation")
    root_turn = await store.create_turn("root", "chat")
    root_turn_id = root_turn["id"]
    await store.update_turn_status(root_turn_id, "waiting_input")
    monkeypatch.setattr(router, "get_session_store", lambda: store)
    attachments = SimpleNamespace(delete_session=AsyncMock())
    learning = SimpleNamespace(detach_session=Mock())
    monkeypatch.setattr(router, "get_attachment_store", lambda: attachments)
    monkeypatch.setattr(router, "LearningStore", lambda: learning)
    turns = SimpleNamespace(cancel_turn_and_wait=AsyncMock(return_value=True))
    monkeypatch.setattr(router, "_turn_runtime_manager", lambda: turns)
    monkeypatch.setattr(
        store,
        "list_active_turns",
        AsyncMock(return_value=[{"id": root_turn_id}]),
    )

    result = await router.delete_session("root")

    assert result == {"deleted": True, "session_id": "root"}
    turns.cancel_turn_and_wait.assert_awaited_once_with(root_turn_id)
    assert await store.get_session("root") is None
    assert await store.get_messages("root") == []
    attachments.delete_session.assert_awaited_once_with("root")
    learning.detach_session.assert_called_once_with("root")


@pytest.mark.asyncio
async def test_delete_keeps_session_when_turn_cancellation_times_out(tmp_path, monkeypatch):
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    await store.create_session(session_id="busy", title="Busy")
    busy_turn = await store.create_turn("busy", "chat")
    busy_turn_id = busy_turn["id"]
    await store.update_turn_status(busy_turn_id, "waiting_input")
    monkeypatch.setattr(router, "get_session_store", lambda: store)
    turns = SimpleNamespace(cancel_turn_and_wait=AsyncMock(return_value=False))
    monkeypatch.setattr(router, "_turn_runtime_manager", lambda: turns)
    monkeypatch.setattr(
        store,
        "list_active_turns",
        AsyncMock(return_value=[{"id": busy_turn_id}]),
    )

    with pytest.raises(HTTPException) as exc:
        await router.delete_session("busy")

    assert exc.value.status_code == 409
    assert await store.get_session("busy") is not None
    assert await store.get_turn(busy_turn_id) is not None
