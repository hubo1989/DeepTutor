import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.services.session.turn_runtime import TurnRuntimeManager


@pytest.mark.asyncio
async def test_cancel_turn_and_wait_bounds_slow_cancellation_cleanup():
    completed = []

    class SlowCancellationRuntime(TurnRuntimeManager):
        async def cancel_turn(self, turn_id: str) -> bool:
            await asyncio.sleep(1.0)
            completed.append(turn_id)
            return True

    runtime = SlowCancellationRuntime(store=SimpleNamespace(get_turn=None))
    result = await asyncio.wait_for(
        runtime.cancel_turn_and_wait("turn-id", timeout_seconds=0.02),
        timeout=0.25,
    )

    assert result is False
    assert completed == []
