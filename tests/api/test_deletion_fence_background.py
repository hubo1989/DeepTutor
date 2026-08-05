from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_fenced_kb_background_task_cannot_recreate_user_workspace(
    auth_isolated_root,
) -> None:
    from deeptutor.api.routers.knowledge import run_upload_processing_task
    from deeptutor.multi_user import identity, paths
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser
    from deeptutor.services import account_lifecycle

    _, record = identity.create_user_if_absent("background@example.com", "hash", role="user")
    owner_id = record["id"]
    account_lifecycle.begin_account_deletion(
        "background@example.com",
        expected_user_id=owner_id,
    )
    user_root = paths.USERS_ROOT / owner_id
    assert not user_root.exists()

    token = set_current_user(
        CurrentUser(
            id=owner_id,
            username="background@example.com",
            role="user",
            scope=paths.scope_for_user(owner_id, is_admin=False),
        )
    )
    try:
        await run_upload_processing_task(
            kb_name="deleted-kb",
            base_dir=str(user_root / "knowledge_bases"),
            uploaded_file_paths=[],
            task_id="kb_upload_fenced_test",
        )
    finally:
        reset_current_user(token)

    assert not user_root.exists()


@pytest.mark.asyncio
async def test_task_manager_cancels_and_awaits_owner_background_tasks() -> None:
    from deeptutor.api.utils.task_id_manager import TaskIDManager

    manager = TaskIDManager.get_instance()
    owner_id = "u_background_cancel_test"
    started = asyncio.Event()
    finished = asyncio.Event()

    async def worker() -> None:
        task = manager.register_current_task("kb_cancel_test", owner_id)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            manager.unregister_current_task(owner_id, task)
            finished.set()

    task = asyncio.create_task(worker())
    await started.wait()
    assert await manager.cancel_all_for_owner(owner_id) == 1
    assert task.done()
    assert finished.is_set()
