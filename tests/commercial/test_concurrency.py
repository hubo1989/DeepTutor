from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from deeptutor.commercial.concurrency import (
    CommercialConcurrencyLimitDenied,
    acquire_commercial_turn_lease,
    commercial_turn_lease,
)
from deeptutor.commercial.entitlement_context import (
    CommercialAccess,
    reset_commercial_access,
    set_commercial_access,
)
from deeptutor.commercial.models import ResolvedEntitlements, SubscriptionStatus
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.session.turn_runtime import TurnRuntimeManager


class _UnusedStore:
    pass


@contextmanager
def _commercial_identity(
    tmp_path,
    monkeypatch,
    *,
    owner_id: str,
    role: str = "user",
    enabled: bool = True,
):
    monkeypatch.setattr(
        "deeptutor.commercial.entitlement_context.get_commercial_runtime",
        lambda: SimpleNamespace(settings=SimpleNamespace(enabled=enabled)),
    )
    user = CurrentUser(
        id=owner_id,
        username=f"{owner_id}@example.com",
        role=role,
        scope=UserScope(
            kind="admin" if role == "admin" else "user",
            user_id=owner_id,
            root=tmp_path / owner_id,
        ),
    )
    now = datetime.now(timezone.utc)
    resolved = ResolvedEntitlements(
        customer_id=f"cus-{owner_id}",
        subscription_id=f"sub-{owner_id}",
        plan_version_id="plan-trial-v1",
        status=SubscriptionStatus.TRIALING,
        active=True,
        resolved_at=now,
        valid_until=now + timedelta(days=1),
        values={
            "capability.chat": True,
            "limits.concurrent_turns": 1,
        },
    )
    user_token = set_current_user(user)
    access_token = set_commercial_access(
        CommercialAccess(owner_id=owner_id, resolved=resolved) if role == "user" else None
    )
    try:
        yield
    finally:
        reset_commercial_access(access_token)
        reset_current_user(user_token)


@pytest.mark.asyncio
async def test_same_owner_is_rejected_across_legacy_and_turn_runtime(tmp_path, monkeypatch) -> None:
    runtime = TurnRuntimeManager(store=_UnusedStore())  # type: ignore[arg-type]
    persistence_called = False

    async def persist(*_args, **_kwargs):
        nonlocal persistence_called
        persistence_called = True

    monkeypatch.setattr(runtime, "_start_turn_impl", persist)

    with _commercial_identity(tmp_path, monkeypatch, owner_id="owner-a"):
        legacy_lease = await acquire_commercial_turn_lease()
        try:
            with pytest.raises(
                CommercialConcurrencyLimitDenied,
                match="^concurrent_turn_limit$",
            ):
                await runtime.start_turn({"capability": "chat", "content": "hello"})
        finally:
            await legacy_lease.release()

    assert persistence_called is False


@pytest.mark.asyncio
async def test_different_owners_have_independent_slots(tmp_path, monkeypatch) -> None:
    with _commercial_identity(tmp_path, monkeypatch, owner_id="owner-a"):
        first = await acquire_commercial_turn_lease()

    try:
        with _commercial_identity(tmp_path, monkeypatch, owner_id="owner-b"):
            second = await acquire_commercial_turn_lease()
            assert second.reserved is True
            await second.release()
    finally:
        await first.release()


@pytest.mark.asyncio
async def test_exception_and_cancellation_release_owner_slot(tmp_path, monkeypatch) -> None:
    with _commercial_identity(tmp_path, monkeypatch, owner_id="owner-release"):
        with pytest.raises(ValueError, match="boom"):
            async with commercial_turn_lease():
                raise ValueError("boom")

        after_exception = await acquire_commercial_turn_lease()
        await after_exception.release()

        entered = asyncio.Event()

        async def hold_slot() -> None:
            async with commercial_turn_lease():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold_slot())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        after_cancel = await acquire_commercial_turn_lease()
        await after_cancel.release()


@pytest.mark.asyncio
async def test_admin_and_disabled_mode_bypass_slots(tmp_path, monkeypatch) -> None:
    with _commercial_identity(
        tmp_path,
        monkeypatch,
        owner_id="admin",
        role="admin",
    ):
        admin_first = await acquire_commercial_turn_lease()
        admin_second = await acquire_commercial_turn_lease()
        assert admin_first.reserved is False
        assert admin_second.reserved is False

    with _commercial_identity(
        tmp_path,
        monkeypatch,
        owner_id="local-user",
        enabled=False,
    ):
        local_first = await acquire_commercial_turn_lease()
        local_second = await acquire_commercial_turn_lease()
        assert local_first.reserved is False
        assert local_second.reserved is False
