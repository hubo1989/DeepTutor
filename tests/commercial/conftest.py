from __future__ import annotations

from datetime import datetime, timezone
import itertools

import pytest

from deeptutor.commercial import CommercialControlPlane, InMemoryCommercialRepository


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def commercial_harness():
    clock = MutableClock(datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc))
    sequence = itertools.count(1)
    repository = InMemoryCommercialRepository()
    service = CommercialControlPlane(
        repository,
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_{next(sequence):04d}",
    )
    return service, repository, clock


async def seed_customer_and_plan(service: CommercialControlPlane):
    customer = await service.ensure_billing_customer(owner_id="user-alice")
    plan = await service.publish_plan_version(
        plan_code="pro",
        version=1,
        name="Pro Monthly",
        price_minor=4900,
        currency="CNY",
        entitlements={
            "capability.deep_solve": True,
            "capability.deep_research": True,
            "quota.llm_tokens": {"limit": 100, "window": "subscription"},
            "quota.parse_pages": {"limit": 10, "window": "subscription"},
        },
    )
    return customer, plan
