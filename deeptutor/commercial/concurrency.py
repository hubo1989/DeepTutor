"""Owner-scoped commercial turn concurrency for the single-process runtime.

The Phase-2 deployment contract runs one API process.  Every consuming entry
point therefore shares this small in-memory lease registry.  A multi-replica
deployment must replace it with a durable/distributed lease without changing
the public ``concurrent_turn_limit`` rejection code.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import threading
from typing import AsyncIterator

from deeptutor.multi_user.context import get_current_user_or_none

from .entitlement_context import (
    CommercialAccessDenied,
    integer_limit,
    require_active_commercial_access,
    require_capability,
)

CONCURRENT_TURN_LIMIT = "concurrent_turn_limit"


class CommercialConcurrencyLimitDenied(RuntimeError):
    """Stable immediate-rejection error for an exhausted owner turn limit."""

    code = CONCURRENT_TURN_LIMIT

    def __init__(self) -> None:
        super().__init__(self.code)


_slots_guard = threading.Lock()
_active_slots: dict[str, int] = {}


@dataclass(slots=True)
class CommercialTurnLease:
    """One idempotently releasable owner slot; bypass leases have no owner."""

    owner_id: str | None = None
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def reserved(self) -> bool:
        return self.owner_id is not None and not self._released

    async def release(self) -> None:
        if self.owner_id is None or self._released:
            return
        with _slots_guard:
            if self._released:
                return
            active = _active_slots.get(self.owner_id, 0)
            if active <= 1:
                _active_slots.pop(self.owner_id, None)
            else:
                _active_slots[self.owner_id] = active - 1
            self._released = True

    async def __aenter__(self) -> CommercialTurnLease:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.release()


async def acquire_commercial_turn_lease(
    *,
    capability: str | None = None,
) -> CommercialTurnLease:
    """Validate fresh request context and immediately reserve one owner slot.

    ``capability`` is supplied by the unified runtime, whose capability names
    map directly to versioned plan entitlements.  Legacy LLM surfaces have no
    one-to-one capability key yet, so they pass ``None`` and still require an
    active Trial/paid subscription.  Disabled commercial mode and platform
    administrators receive a no-op lease.
    """

    access = (
        require_capability(capability)
        if capability is not None
        else require_active_commercial_access()
    )
    if access is None:
        return CommercialTurnLease()

    user = get_current_user_or_none()
    if user is None or user.id != access.owner_id:
        raise CommercialAccessDenied(
            "subscription_required",
            "The active subscription does not belong to the current user.",
        )

    limit = integer_limit("concurrent_turns")
    if limit is None:
        return CommercialTurnLease()

    owner_id = access.owner_id
    with _slots_guard:
        active = _active_slots.get(owner_id, 0)
        if active >= limit:
            raise CommercialConcurrencyLimitDenied()
        _active_slots[owner_id] = active + 1
    return CommercialTurnLease(owner_id=owner_id)


@asynccontextmanager
async def commercial_turn_lease(
    *,
    capability: str | None = None,
) -> AsyncIterator[CommercialTurnLease]:
    """Acquire and unconditionally release a consuming-operation lease."""

    lease = await acquire_commercial_turn_lease(capability=capability)
    try:
        yield lease
    finally:
        await lease.release()


__all__ = [
    "CONCURRENT_TURN_LIMIT",
    "CommercialConcurrencyLimitDenied",
    "CommercialTurnLease",
    "acquire_commercial_turn_lease",
    "commercial_turn_lease",
]
