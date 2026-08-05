from __future__ import annotations

from datetime import timedelta

import pytest

from deeptutor.commercial import ImmutablePlanVersion, SubscriptionStatus

from .conftest import seed_customer_and_plan


@pytest.mark.asyncio
async def test_plan_version_publish_is_idempotent_but_content_is_immutable(
    commercial_harness,
) -> None:
    service, _repository, _clock = commercial_harness
    _customer, first = await seed_customer_and_plan(service)

    same = await service.publish_plan_version(
        plan_code="pro",
        version=1,
        name="Pro Monthly",
        price_minor=4900,
        currency="cny",
        entitlements={
            "capability.deep_solve": True,
            "capability.deep_research": True,
            "quota.llm_tokens": {"limit": 100, "window": "subscription"},
            "quota.parse_pages": {"limit": 10, "window": "subscription"},
        },
    )
    assert same == first

    with pytest.raises(ImmutablePlanVersion):
        await service.publish_plan_version(
            plan_code="pro",
            version=1,
            name="Pro Monthly",
            price_minor=9900,
            currency="CNY",
            entitlements={"capability.deep_solve": True},
        )


@pytest.mark.asyncio
async def test_effective_entitlements_are_derived_from_temporally_valid_subscription(
    commercial_harness,
) -> None:
    service, _repository, clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)

    before = await service.resolve_entitlements(customer.id)
    assert not before.active
    assert before.values == {}

    trial = await service.ensure_trial(customer.id, plan.id)
    during = await service.resolve_entitlements(customer.id)
    assert during.active
    assert during.subscription_id == trial.id
    assert during.allows("capability.deep_solve")
    assert during.quota("llm_tokens").limit == 100

    # Access expires from source-of-truth timestamps even before the persisted
    # subscription status is repaired by reconciliation.
    clock.value = trial.trial_ends_at + timedelta(seconds=1)
    after = await service.resolve_entitlements(customer.id)
    assert not after.active
    assert after.values == {}


@pytest.mark.asyncio
async def test_canceled_and_past_due_subscriptions_do_not_resolve_entitlements(
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
    assert not (await service.resolve_entitlements(customer.id)).active


@pytest.mark.asyncio
async def test_entitlement_snapshot_cannot_mutate_plan_definition(commercial_harness) -> None:
    service, repository, _clock = commercial_harness
    customer, plan = await seed_customer_and_plan(service)
    await service.ensure_trial(customer.id, plan.id)
    snapshot = await service.resolve_entitlements(customer.id)

    with pytest.raises(TypeError):
        snapshot.values["capability.deep_solve"] = False  # type: ignore[index]

    stored = await repository.list_entitlements(plan.id)
    assert {item.key: item.value for item in stored}["capability.deep_solve"] is True
