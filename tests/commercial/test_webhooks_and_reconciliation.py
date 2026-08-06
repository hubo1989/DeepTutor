from __future__ import annotations

from datetime import timedelta

import pytest

from deeptutor.commercial import IdempotencyConflict, SubscriptionStatus, UsageReservationState
from deeptutor.commercial.keys import webhook_event_dedupe_key

from .conftest import seed_customer_and_plan


@pytest.mark.asyncio
async def test_webhook_inbox_deduplicates_provider_event_and_detects_payload_drift(
    commercial_harness,
) -> None:
    service, _repository, _clock = commercial_harness
    first = await service.record_webhook_event(
        provider="Stripe",
        external_event_id="evt_123",
        event_type="customer.subscription.updated",
        payload={"id": "evt_123", "data": {"status": "active"}},
    )
    retry = await service.record_webhook_event(
        provider="stripe",
        external_event_id="evt_123",
        event_type="customer.subscription.updated",
        payload={"data": {"status": "active"}, "id": "evt_123"},
    )
    assert retry == first

    with pytest.raises(IdempotencyConflict):
        await service.record_webhook_event(
            provider="stripe",
            external_event_id="evt_123",
            event_type="customer.subscription.updated",
            payload={"id": "evt_123", "data": {"status": "canceled"}},
        )


@pytest.mark.asyncio
async def test_webhook_processing_marker_is_retry_safe(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    event = await service.record_webhook_event(
        provider="stripe",
        external_event_id="evt_1",
        event_type="invoice.paid",
        payload={"id": "evt_1"},
    )
    first = await service.mark_webhook_processed(event.id)
    second = await service.mark_webhook_processed(event.id)

    assert first == second
    assert first.processed_at is not None


def test_webhook_dedupe_key_normalizes_provider_but_not_external_event_identity() -> None:
    assert webhook_event_dedupe_key("Stripe", "evt_A") == webhook_event_dedupe_key(
        " stripe ", "evt_A"
    )
    assert webhook_event_dedupe_key("stripe", "evt_A") != webhook_event_dedupe_key(
        "stripe", "evt_a"
    )


@pytest.mark.asyncio
async def test_reconcile_reports_then_repairs_expired_trial_and_stale_reservation(
    commercial_harness,
) -> None:
    service, repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    trial = await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(
        customer.id,
        "llm_tokens",
        20,
        "request-that-crashed",
        expires_in=timedelta(minutes=5),
    )
    clock.value = trial.trial_ends_at + timedelta(seconds=1)

    audit = await service.reconcile(repair=False)
    assert {finding.code for finding in audit.findings} == {
        "expired_trial_not_materialized",
        "stale_usage_reservation",
    }
    assert audit.repaired == 0

    repaired = await service.reconcile(repair=True)
    assert repaired.repaired == 2
    assert (await repository.get_subscription(trial.id)).status is SubscriptionStatus.EXPIRED
    assert (
        await repository.get_usage_reservation(reservation.id)
    ).state is UsageReservationState.RELEASED

    clean = await service.reconcile(repair=False)
    assert clean.findings == ()
