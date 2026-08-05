"""Tests for the RunManager — start/cancel/replay semantics."""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.services.memory.consolidator.runs import (
    RunBusyError,
    RunManager,
    push_undo_checkpoint,
)


@pytest.fixture()
def manager() -> RunManager:
    return RunManager()


@pytest.mark.asyncio
async def test_start_runs_to_completion_and_buffers_events(manager: RunManager) -> None:
    async def runner(on_event):
        await on_event({"stage": "progress", "turn": 1})
        await on_event({"stage": "progress", "turn": 2})

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    if run._task is not None:
        await run._task
    assert run.status == "done"
    stages = [ev.payload.get("stage") for ev in run.events]
    assert "run_started" in stages
    assert "progress" in stages
    assert stages[-1] == "run_ended"


@pytest.mark.asyncio
async def test_busy_error_when_concurrent_start_same_doc(manager: RunManager) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(on_event):
        started.set()
        await release.wait()

    first = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    await started.wait()

    with pytest.raises(RunBusyError):
        await manager.start(layer="L2", key="chat", mode="audit", runner=runner)

    # Concurrent run on a *different* doc is fine.
    second = await manager.start(layer="L2", key="notebook", mode="update", runner=runner)
    release.set()
    if first._task is not None:
        await first._task
    if second._task is not None:
        await second._task


@pytest.mark.asyncio
async def test_cancel_marks_run_cancelled(manager: RunManager) -> None:
    started = asyncio.Event()

    async def runner(on_event):
        started.set()
        await asyncio.sleep(10)  # would hang forever; cancellation interrupts

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    await started.wait()
    cancelled = await manager.cancel(run.id)
    assert cancelled is True
    if run._task is not None:
        # The driver swallows CancelledError into a terminal status.
        await asyncio.gather(run._task, return_exceptions=True)
    assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_all_for_owner_awaits_only_that_owners_runs(
    manager: RunManager,
) -> None:
    started = {"alice": asyncio.Event(), "bob": asyncio.Event()}

    def runner_for(owner: str):
        async def runner(_on_event):
            started[owner].set()
            await asyncio.Event().wait()

        return runner

    alice = await manager.start(
        owner_id="u_alice",
        layer="L2",
        key="chat",
        mode="update",
        runner=runner_for("alice"),
    )
    bob = await manager.start(
        owner_id="u_bob",
        layer="L2",
        key="chat",
        mode="update",
        runner=runner_for("bob"),
    )
    await asyncio.gather(started["alice"].wait(), started["bob"].wait())

    assert await manager.cancel_all_for_owner("u_alice") == 1
    assert alice.status == "cancelled"
    assert bob.active is True
    await manager.cancel_all_for_owner("u_bob")


@pytest.mark.asyncio
async def test_wait_for_events_replays_from_cursor(manager: RunManager) -> None:
    async def runner(on_event):
        for i in range(5):
            await on_event({"stage": "progress", "turn": i})

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    if run._task is not None:
        await run._task

    all_events = await manager.wait_for_events(run, since=0)
    assert len(all_events) >= 5  # run_started + 5 progress + run_ended

    tail = await manager.wait_for_events(run, since=3)
    assert tail[0].seq == 3


@pytest.mark.asyncio
async def test_active_for_returns_none_after_done(manager: RunManager) -> None:
    async def runner(on_event):
        await on_event({"stage": "progress"})

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    if run._task is not None:
        await run._task
    assert manager.active_for("L2", "chat") is None
    assert manager.get(run.id) is not None  # but still in history


@pytest.mark.asyncio
async def test_run_records_error_when_runner_raises(manager: RunManager) -> None:
    async def runner(on_event):
        raise RuntimeError("kaboom")

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    if run._task is not None:
        await run._task
    assert run.status == "error"
    assert "kaboom" in (run.error or "")


@pytest.mark.asyncio
async def test_undo_last_restores_previous_document(manager: RunManager, tmp_path) -> None:
    path = tmp_path / "chat.md"
    path.write_text("before", encoding="utf-8")

    async def runner(on_event):
        previous = path.read_text(encoding="utf-8")
        path.write_text("after", encoding="utf-8")
        depth = push_undo_checkpoint(
            layer="L2",
            key="chat",
            path=path,
            existed=True,
            previous_content=previous,
            action="test_write",
            turn=1,
            label="update",
        )
        await on_event({"stage": "doc_updated", "undo_depth": depth})

    run = await manager.start(layer="L2", key="chat", mode="update", runner=runner)
    if run._task is not None:
        await run._task

    assert path.read_text(encoding="utf-8") == "after"
    assert run.undo_stack
    event = await manager.undo_last(run.id)
    assert event is not None
    assert path.read_text(encoding="utf-8") == "before"
    assert event.payload["stage"] == "undo_applied"


@pytest.mark.asyncio
async def test_owner_scopes_active_runs_lookup_list_cancel_and_undo(tmp_path) -> None:
    manager = RunManager()
    alice_started = asyncio.Event()

    async def blocking_runner(_on_event):
        alice_started.set()
        await asyncio.Event().wait()

    alice_run = await manager.start(
        owner_id="u_alice",
        layer="L2",
        key="chat",
        mode="update",
        runner=blocking_runner,
    )
    await alice_started.wait()

    # A different owner can run the same logical document key because the
    # actual memory documents live under different workspace roots.
    async def finished_runner(_on_event):
        return None

    bob_run = await manager.start(
        owner_id="u_bob",
        layer="L2",
        key="chat",
        mode="update",
        runner=finished_runner,
    )
    if bob_run._task is not None:
        await bob_run._task

    assert manager.get(alice_run.id, owner_id="u_bob") is None
    assert manager.active_for("L2", "chat", owner_id="u_bob") is None
    assert manager.list_for(owner_id="u_bob") == [bob_run]
    assert await manager.cancel(alice_run.id, owner_id="u_bob") is False
    with pytest.raises(KeyError):
        await manager.undo_last(alice_run.id, owner_id="u_bob")

    assert await manager.cancel(alice_run.id, owner_id="u_alice") is True
    if alice_run._task is not None:
        await asyncio.gather(alice_run._task, return_exceptions=True)
