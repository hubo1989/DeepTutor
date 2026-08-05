from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.commercial.entitlement_context import CommercialAccessDenied
from deeptutor.services.session.turn_runtime import TurnRuntimeManager


class _UnusedStore:
    """The wrapper tests replace the persistence implementation entirely."""


@pytest.mark.asyncio
async def test_turn_validates_config_before_acquiring_shared_slot(monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]

    async def unexpected_acquire(*_args, **_kwargs):
        raise AssertionError("invalid config must not reserve owner capacity")

    monkeypatch.setattr(
        "deeptutor.commercial.concurrency.acquire_commercial_turn_lease",
        unexpected_acquire,
    )

    with pytest.raises(RuntimeError, match="Invalid chat config"):
        await runtime.start_turn(
            {
                "capability": "chat",
                "config": {"unknown_public_field": True},
            }
        )


@pytest.mark.asyncio
async def test_turn_gate_rejects_inactive_before_persistence(monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]
    persistence_called = False

    def reject(_capability: str):
        raise CommercialAccessDenied("trial_expired", "expired")

    async def persist(*_args, **_kwargs):
        nonlocal persistence_called
        persistence_called = True

    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.require_capability",
        reject,
    )
    monkeypatch.setattr(runtime, "_start_turn_impl", persist)

    with pytest.raises(RuntimeError, match="^trial_expired$"):
        await runtime.start_turn({"capability": "chat", "content": "hello"})
    assert persistence_called is False


@pytest.mark.asyncio
async def test_turn_runtime_passes_shared_lease_to_execution(monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]
    released = False

    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.require_capability",
        lambda _capability: SimpleNamespace(resolved=SimpleNamespace(values={})),
    )

    class Lease:
        reserved = True

        async def release(self):
            nonlocal released
            released = True

    lease = Lease()

    async def acquire(*, capability: str | None = None):
        assert capability == "chat"
        return lease

    monkeypatch.setattr(
        "deeptutor.commercial.concurrency.acquire_commercial_turn_lease",
        acquire,
    )

    async def persist(_payload, *, commercial_lease):
        assert commercial_lease is lease
        return {"id": "session-1"}, {"id": "turn-1"}

    monkeypatch.setattr(runtime, "_start_turn_impl", persist)
    await runtime.start_turn({"session_id": "one"})
    assert released is False


@pytest.mark.asyncio
async def test_failed_start_releases_reserved_trial_slot(monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]
    released = False
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.require_capability",
        lambda _capability: SimpleNamespace(resolved=SimpleNamespace(values={})),
    )

    class Lease:
        reserved = True

        async def release(self):
            nonlocal released
            released = True

    lease = Lease()

    async def acquire(*, capability: str | None = None):
        assert capability == "chat"
        return lease

    monkeypatch.setattr(
        "deeptutor.commercial.concurrency.acquire_commercial_turn_lease",
        acquire,
    )

    async def fail(_payload, *, commercial_lease):
        assert commercial_lease is lease
        raise ValueError("persistence failed")

    monkeypatch.setattr(runtime, "_start_turn_impl", fail)
    with pytest.raises(ValueError, match="persistence failed"):
        await runtime.start_turn({})
    assert released is True


@pytest.mark.asyncio
async def test_trial_rejects_explicit_manim_before_turn_creation(monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]
    persistence_called = False
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.require_capability",
        lambda _capability: SimpleNamespace(
            resolved=SimpleNamespace(values={"feature.manim": False})
        ),
    )

    async def persist(*_args, **_kwargs):
        nonlocal persistence_called
        persistence_called = True

    monkeypatch.setattr(runtime, "_start_turn_impl", persist)
    with pytest.raises(RuntimeError, match="^feature_not_in_plan$"):
        await runtime.start_turn(
            {
                "capability": "visualize",
                "config": {"render_mode": "manim_video"},
            }
        )
    assert persistence_called is False
