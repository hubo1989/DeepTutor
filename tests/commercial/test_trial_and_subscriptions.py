from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from deeptutor.commercial import (
    ActiveSubscriptionExists,
    InvalidSubscriptionTransition,
    SubscriptionStatus,
    TrialAlreadyClaimed,
)

from .conftest import seed_customer_and_plan


@pytest.mark.asyncio
async def test_ensure_trial_is_exactly_seven_days_and_idempotent(commercial_harness) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)

    first = await service.ensure_trial(customer.id, plan.id)
    second = await service.ensure_trial(customer.id, plan.id)

    assert first == second
    assert first.status is SubscriptionStatus.TRIALING
    assert first.trial_started_at == clock.value
    assert first.trial_ends_at == clock.value + timedelta(days=7)
    assert first.current_period_end == first.trial_ends_at


@pytest.mark.asyncio
async def test_concurrent_trial_retries_create_one_subscription(commercial_harness) -> None:
    service, repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)

    trials = await asyncio.gather(*(service.ensure_trial(customer.id, plan.id) for _ in range(20)))

    assert {trial.id for trial in trials} == {trials[0].id}
    assert len(await repository.list_subscriptions(customer.id)) == 1


@pytest.mark.asyncio
async def test_trial_cannot_be_claimed_again_for_a_different_plan(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, first_plan = await seed_customer_and_plan(service)
    second_plan = await service.publish_plan_version(
        plan_code="starter",
        version=1,
        name="Starter Monthly",
        price_minor=1900,
        currency="CNY",
        entitlements={"capability.deep_solve": True},
    )
    await service.ensure_trial(customer.id, first_plan.id)

    with pytest.raises(TrialAlreadyClaimed):
        await service.ensure_trial(customer.id, second_plan.id)


@pytest.mark.asyncio
async def test_trial_is_not_created_while_paid_subscription_is_active(commercial_harness) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.create_active_subscription(
        customer.id,
        plan.id,
        external_subscription_id="sub_paid_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )

    with pytest.raises(ActiveSubscriptionExists):
        await service.ensure_trial(customer.id, plan.id)


@pytest.mark.asyncio
async def test_only_one_trialing_or_active_subscription_per_customer(commercial_harness) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.create_active_subscription(
        customer.id,
        plan.id,
        external_subscription_id="sub_paid_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )

    with pytest.raises(ActiveSubscriptionExists):
        await service.create_active_subscription(
            customer.id,
            plan.id,
            external_subscription_id="sub_paid_2",
            current_period_start=clock.value,
            current_period_end=clock.value + timedelta(days=30),
        )


@pytest.mark.asyncio
async def test_past_due_still_blocks_a_second_active_like_subscription(
    commercial_harness,
) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    paid = await service.create_active_subscription(
        customer.id,
        plan.id,
        external_subscription_id="sub_paid_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )
    await service.transition_subscription(paid.id, SubscriptionStatus.PAST_DUE)

    with pytest.raises(ActiveSubscriptionExists):
        await service.create_active_subscription(
            customer.id,
            plan.id,
            external_subscription_id="sub_paid_2",
            current_period_start=clock.value,
            current_period_end=clock.value + timedelta(days=30),
        )


@pytest.mark.asyncio
async def test_subscription_state_machine_allows_only_declared_transitions(
    commercial_harness,
) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    trial = await service.ensure_trial(customer.id, plan.id)

    expired = await service.transition_subscription(trial.id, SubscriptionStatus.EXPIRED)
    assert expired.status is SubscriptionStatus.EXPIRED
    with pytest.raises(InvalidSubscriptionTransition):
        await service.transition_subscription(expired.id, SubscriptionStatus.ACTIVE)

    paid = await service.create_active_subscription(
        customer.id,
        plan.id,
        external_subscription_id="sub_paid_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )
    past_due = await service.transition_subscription(paid.id, SubscriptionStatus.PAST_DUE)
    assert past_due.status is SubscriptionStatus.PAST_DUE
    recovered = await service.transition_subscription(past_due.id, SubscriptionStatus.ACTIVE)
    assert recovered.status is SubscriptionStatus.ACTIVE
    canceled = await service.transition_subscription(recovered.id, SubscriptionStatus.CANCELED)
    assert canceled.status is SubscriptionStatus.CANCELED

    with pytest.raises(InvalidSubscriptionTransition):
        await service.transition_subscription(canceled.id, SubscriptionStatus.EXPIRED)

    with pytest.raises(InvalidSubscriptionTransition):
        await service.transition_subscription(canceled.id, SubscriptionStatus.ACTIVE)


@pytest.mark.asyncio
async def test_transition_retry_to_current_state_is_idempotent(commercial_harness) -> None:
    service, _repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    trial = await service.ensure_trial(customer.id, plan.id)

    first = await service.transition_subscription(trial.id, SubscriptionStatus.CANCELED)
    second = await service.transition_subscription(trial.id, SubscriptionStatus.CANCELED)

    assert first == second


@pytest.mark.asyncio
async def test_paid_activation_atomically_ends_trial_and_creates_provider_subscription(
    commercial_harness,
) -> None:
    service, repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    trial = await service.ensure_trial(customer.id, plan.id)

    paid = await service.activate_paid_subscription(
        customer.id,
        plan.id,
        provider="stripe",
        external_subscription_id="sub_provider_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )

    assert paid.status is SubscriptionStatus.ACTIVE
    assert paid.id != trial.id
    assert (await repository.get_subscription(trial.id)).status is SubscriptionStatus.CANCELED
    assert (await service.resolve_entitlements(customer.id)).subscription_id == paid.id

    # Provider retry returns the same row and never opens a two-call access gap.
    retry = await service.activate_paid_subscription(
        customer.id,
        plan.id,
        provider="stripe",
        external_subscription_id="sub_provider_1",
        current_period_start=clock.value,
        current_period_end=clock.value + timedelta(days=30),
    )
    assert retry == paid
