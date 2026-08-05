from __future__ import annotations

import asyncio

import pytest

from deeptutor.commercial import (
    EntitlementDenied,
    IdempotencyConflict,
    UsageAmountExceeded,
    UsageReservationState,
)
from deeptutor.commercial.keys import request_dedupe_key

from .conftest import seed_customer_and_plan


@pytest.mark.asyncio
async def test_reserve_finalize_and_finalize_retry_are_idempotent(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)

    reservation = await service.reserve_usage(
        customer.id,
        meter="llm_tokens",
        quantity=80,
        request_id="turn-123/provider-call-1",
    )
    retry = await service.reserve_usage(
        customer.id,
        meter="llm_tokens",
        quantity=80,
        request_id="turn-123/provider-call-1",
    )
    assert retry == reservation

    event = await service.finalize_usage(
        reservation.id,
        actual_quantity=55,
        provider="openai",
        model="gpt-5-mini",
        usage_units={"input_tokens": 40, "output_tokens": 15},
        price_version="openai-2026-08-01",
        cost_micros=1250,
        is_estimated=False,
    )
    same_event = await service.finalize_usage(
        reservation.id,
        actual_quantity=55,
        provider="openai",
        model="gpt-5-mini",
        usage_units={"output_tokens": 15, "input_tokens": 40},
        price_version="openai-2026-08-01",
        cost_micros=1250,
        is_estimated=False,
    )
    assert same_event == event
    assert event.quantity == 55
    assert event.provider == "openai"
    assert event.model == "gpt-5-mini"
    assert dict(event.usage_units) == {"input_tokens": 40, "output_tokens": 15}
    assert event.price_version == "openai-2026-08-01"
    assert event.cost_micros == 1250
    assert event.is_estimated is False

    with pytest.raises(IdempotencyConflict):
        await service.finalize_usage(reservation.id, actual_quantity=54)


@pytest.mark.asyncio
async def test_reservation_retry_with_changed_semantics_is_rejected(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    await service.reserve_usage(
        customer.id,
        meter="llm_tokens",
        quantity=50,
        request_id="same-request",
    )

    with pytest.raises(IdempotencyConflict):
        await service.reserve_usage(
            customer.id,
            meter="llm_tokens",
            quantity=60,
            request_id="same-request",
        )


@pytest.mark.asyncio
async def test_reservations_are_atomic_against_plan_quota(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)

    results = await asyncio.gather(
        service.reserve_usage(customer.id, "llm_tokens", 60, "request-a"),
        service.reserve_usage(customer.id, "llm_tokens", 60, "request-b"),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, EntitlementDenied) for result in results) == 1


@pytest.mark.asyncio
async def test_release_is_idempotent_and_frees_reserved_capacity(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(customer.id, "llm_tokens", 100, "request-to-release")

    released = await service.release_usage(reservation.id)
    released_again = await service.release_usage(reservation.id)
    assert released == released_again
    assert released.state is UsageReservationState.RELEASED

    replacement = await service.reserve_usage(customer.id, "llm_tokens", 100, "replacement-request")
    assert replacement.state is UsageReservationState.RESERVED


@pytest.mark.asyncio
async def test_finalized_reservation_cannot_be_released(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(customer.id, "llm_tokens", 20, "request-1")
    await service.finalize_usage(reservation.id, actual_quantity=10)

    with pytest.raises(IdempotencyConflict):
        await service.release_usage(reservation.id)


@pytest.mark.asyncio
async def test_actual_usage_cannot_exceed_reserved_quantity(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(customer.id, "llm_tokens", 20, "request-1")

    with pytest.raises(UsageAmountExceeded):
        await service.finalize_usage(reservation.id, actual_quantity=21)


@pytest.mark.asyncio
async def test_missing_meter_entitlement_denies_reservation(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)

    with pytest.raises(EntitlementDenied):
        await service.reserve_usage(customer.id, "gpu_seconds", 1, "gpu-request")


@pytest.mark.asyncio
async def test_daily_quota_resets_at_server_computed_utc_day_boundary(commercial_harness) -> None:
    service, _repository, clock = commercial_harness
    customer = await service.ensure_billing_customer(owner_id="daily-user")
    plan = await service.publish_plan_version(
        plan_code="daily-test",
        version=1,
        name="Daily test",
        price_minor=0,
        currency="CNY",
        entitlements={
            "quota.llm_tokens": {"limit": 100, "window": "utc_day"},
        },
    )
    await service.ensure_trial(customer.id, plan.id)
    first = await service.reserve_usage(customer.id, "llm_tokens", 100, "day-one")
    await service.finalize_usage(first.id, actual_quantity=100)

    with pytest.raises(EntitlementDenied):
        await service.reserve_usage(customer.id, "llm_tokens", 1, "same-day")

    clock.value = clock.value.replace(day=clock.value.day + 1, hour=0, minute=1)
    next_day = await service.reserve_usage(customer.id, "llm_tokens", 100, "day-two")
    assert next_day.period_start.hour == 0
    assert next_day.period_start.minute == 0


@pytest.mark.asyncio
async def test_usage_cost_fields_validate_fail_closed(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    reservation = await service.reserve_usage(customer.id, "llm_tokens", 20, "request-1")

    with pytest.raises(ValueError, match="cost_micros"):
        await service.finalize_usage(
            reservation.id,
            actual_quantity=10,
            cost_micros=-1,
        )


def test_request_dedupe_key_is_stable_scoped_and_does_not_expose_raw_identifier() -> None:
    first = request_dedupe_key("usage-reserve", "customer-1", "secret-request-id")
    second = request_dedupe_key("usage-reserve", "customer-1", "secret-request-id")
    different_scope = request_dedupe_key("webhook", "customer-1", "secret-request-id")

    assert first == second
    assert first != different_scope
    assert first.startswith("req_v1_")
    assert "secret-request-id" not in first
