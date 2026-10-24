"""The commercial runtime's periodic reconcile janitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from deeptutor.commercial.runtime import (
    DEFAULT_RECONCILE_INTERVAL_SECONDS,
    CommercialRuntime,
    CommercialSettings,
)
from deeptutor.commercial.seed import TrialV1ModelBinding

ENABLED_ENV = {
    "DEEPTUTOR_COMMERCIAL_ENABLED": "true",
    "DEEPTUTOR_COMMERCIAL_DATABASE_URL": "postgresql://localhost/commercial",
    "DEEPTUTOR_COMMERCIAL_TRIAL_LLM_MODELS": ('{"profile_id": "trial", "model_ids": ["m-1"]}'),
}


def test_reconcile_interval_defaults_and_disables_via_env():
    default = CommercialSettings.from_env(ENABLED_ENV)
    assert default.reconcile_interval_seconds == DEFAULT_RECONCILE_INTERVAL_SECONDS

    disabled = CommercialSettings.from_env(
        {**ENABLED_ENV, "DEEPTUTOR_COMMERCIAL_RECONCILE_INTERVAL_SECONDS": "0"}
    )
    assert disabled.reconcile_interval_seconds == 0.0

    custom = CommercialSettings.from_env(
        {**ENABLED_ENV, "DEEPTUTOR_COMMERCIAL_RECONCILE_INTERVAL_SECONDS": "45.5"}
    )
    assert custom.reconcile_interval_seconds == 45.5


def test_reconcile_interval_rejects_garbage_and_negative():
    with pytest.raises(Exception, match="RECONCILE_INTERVAL"):
        CommercialSettings.from_env(
            {**ENABLED_ENV, "DEEPTUTOR_COMMERCIAL_RECONCILE_INTERVAL_SECONDS": "soon"}
        )
    with pytest.raises(Exception, match="negative"):
        CommercialSettings.from_env(
            {**ENABLED_ENV, "DEEPTUTOR_COMMERCIAL_RECONCILE_INTERVAL_SECONDS": "-1"}
        )


@dataclass
class _RecordingControlPlane:
    calls: list[tuple[datetime, bool]] = field(default_factory=list)
    error: Exception | None = None

    async def reconcile(self, at: datetime, *, repair: bool):
        self.calls.append((at, repair))
        if self.error is not None:
            raise self.error
        from deeptutor.commercial.models import ReconciliationReport

        return ReconciliationReport(checked_at=at, findings=(), repaired=0)


def _runtime_with(interval: float) -> CommercialRuntime:
    runtime = CommercialRuntime(
        CommercialSettings(
            enabled=True,
            database_url="postgresql://localhost/commercial",
            trial_model_binding=TrialV1ModelBinding(profile_id="trial", model_ids=("m-1",)),
            reconcile_interval_seconds=interval,
        )
    )
    return runtime


@pytest.mark.asyncio
async def test_sweep_applies_repair_with_utc_clock():
    runtime = _runtime_with(300.0)
    control_plane = _RecordingControlPlane()
    runtime.control_plane = control_plane  # type: ignore[assignment]

    await runtime.run_reconcile_sweep()

    assert len(control_plane.calls) == 1
    at, repair = control_plane.calls[0]
    assert repair is True
    assert at.tzinfo is not None


@pytest.mark.asyncio
async def test_sweep_swallows_failures_to_survive_the_next_tick():
    runtime = _runtime_with(300.0)
    control_plane = _RecordingControlPlane()
    control_plane.error = RuntimeError("ledger unavailable")
    runtime.control_plane = control_plane  # type: ignore[assignment]

    await runtime.run_reconcile_sweep()  # must not raise

    assert len(control_plane.calls) == 1
