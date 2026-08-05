"""Authorization tests for knowledge task SSE streams."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
import pytest

from deeptutor.api.utils.task_id_manager import TaskIDManager
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope


@pytest.fixture
def task_manager(monkeypatch) -> TaskIDManager:
    monkeypatch.setattr(TaskIDManager, "_instance", None)
    monkeypatch.setattr(TaskIDManager, "_task_ids", {})
    monkeypatch.setattr(TaskIDManager, "_task_metadata", {})
    monkeypatch.setattr(TaskIDManager, "_active_async_tasks", {})
    return TaskIDManager.get_instance()


def _user(user_id: str, *, admin: bool = False) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=f"{user_id}@example.com",
        role="admin" if admin else "user",
        scope=UserScope(
            kind="admin" if admin else "user",
            user_id=user_id,
            root=Path("/tmp") / user_id,
        ),
    )


async def _open_stream_as(task_id: str, user: CurrentUser):  # noqa: ANN202
    from deeptutor.api.routers.knowledge import stream_task_logs

    token = set_current_user(user)
    try:
        return await stream_task_logs(task_id)
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_knowledge_task_stream_rejects_another_owner(task_manager) -> None:
    task_id = task_manager.generate_task_id(
        "kb_upload",
        "alice-upload",
        owner_id="u_alice",
    )

    with pytest.raises(HTTPException) as exc_info:
        await _open_stream_as(task_id, _user("u_mallory"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Task not found"


@pytest.mark.asyncio
async def test_knowledge_task_stream_allows_owner(task_manager) -> None:
    task_id = task_manager.generate_task_id(
        "kb_upload",
        "alice-upload",
        owner_id="u_alice",
    )

    response = await _open_stream_as(task_id, _user("u_alice"))

    assert response.status_code == 200
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_knowledge_task_stream_allows_admin(task_manager) -> None:
    task_id = task_manager.generate_task_id(
        "kb_upload",
        "alice-upload",
        owner_id="u_alice",
    )

    response = await _open_stream_as(task_id, _user("u_admin", admin=True))

    assert response.status_code == 200
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_knowledge_task_stream_rejects_unknown_task_for_admin(task_manager) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _open_stream_as("unknown-task", _user("u_admin", admin=True))

    assert exc_info.value.status_code == 404


def test_task_owner_cannot_be_rebound(task_manager) -> None:
    task_id = task_manager.generate_task_id(
        "kb_upload",
        "alice-upload",
        owner_id="u_alice",
    )

    with pytest.raises(PermissionError, match="another user"):
        task_manager.generate_task_id(
            "kb_upload",
            "alice-upload",
            owner_id="u_mallory",
        )

    assert task_manager.get_task_metadata(task_id)["owner_id"] == "u_alice"
