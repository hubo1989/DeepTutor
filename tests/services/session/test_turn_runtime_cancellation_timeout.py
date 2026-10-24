import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.app.service import TurnApplicationService


@pytest.mark.asyncio
async def test_cancel_turn_and_wait_bounds_slow_cancellation_cleanup():
    completed = []

    class SlowCancellationRuntime(TurnApplicationService):
        async def cancel_turn(self, turn_id: str, *, command_id: str | None = None) -> bool:
            await asyncio.sleep(1.0)
            completed.append(turn_id)
            return True

    runtime = SlowCancellationRuntime(
        store_provider=SimpleNamespace(),
        runtime_registry=SimpleNamespace(),
        coordinator=SimpleNamespace(),
    )
    result = await asyncio.wait_for(
        runtime.cancel_turn_and_wait("turn-id", timeout_seconds=0.02),
        timeout=0.25,
    )

    assert result is False
    assert completed == []
