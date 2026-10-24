"""Read-only per-meter usage summary (trial quota visibility).

The trial UI shows "used / limit" per meter inside the current quota window.
These tests pin the accounting rules it borrows from enforcement: reserved
amounts are held until finalized, a finalize is counted exactly once, and
different meters never mix.
"""

from __future__ import annotations

import pytest

from deeptutor.commercial.memory import InMemoryCommercialRepository
from deeptutor.commercial.service import CommercialControlPlane


async def _metered_plane(*, llm_limit: int | None = 100_000, window: str = "utc_day"):
    repository = InMemoryCommercialRepository()
    control_plane = CommercialControlPlane(repository)
    customer = await control_plane.ensure_billing_customer(owner_id="u-trial")
    plan = await control_plane.publish_plan_version(
        plan_code="trial-usage",
        version=1,
        name="Trial usage test",
        price_minor=0,
        currency="CNY",
        entitlements={
            "quota.llm_tokens": {"limit": llm_limit, "window": window},
            "quota.embedding_tokens": {"limit": 500_000, "window": "subscription"},
        },
    )
    subscription = await control_plane.ensure_trial(customer.id, plan.id)
    assert subscription is not None
    return control_plane, customer


@pytest.mark.asyncio
async def test_summary_counts_reservations_until_finalized():
    control_plane, customer = await _metered_plane()

    reservation = await control_plane.reserve_usage(customer.id, "llm_tokens", 30, "req-1")
    summary = await control_plane.usage_summary(customer.id)
    assert summary["llm_tokens"]["limit"] == 100_000
    assert summary["llm_tokens"]["window"] == "utc_day"
    assert summary["llm_tokens"]["used"] == 0
    assert summary["llm_tokens"]["reserved"] == 30
    # Embedding is metered separately and untouched.
    assert summary["embedding_tokens"]["used"] == 0

    await control_plane.finalize_usage(reservation.id, actual_quantity=25)
    summary = await control_plane.usage_summary(customer.id)
    # Finalized reservation is not double-counted alongside its usage event.
    assert summary["llm_tokens"]["used"] == 25
    assert summary["llm_tokens"]["reserved"] == 0


@pytest.mark.asyncio
async def test_summary_distinguishes_unlimited_from_deny_all():
    # Commercial semantics: limit=None is unlimited, limit=0 denies all usage
    # (unlike the legacy multi-user quota where 0 means unlimited).
    unlimited, unlimited_customer = await _metered_plane(llm_limit=None)
    assert (await unlimited.usage_summary(unlimited_customer.id))["llm_tokens"]["limit"] is None

    deny_all, deny_customer = await _metered_plane(llm_limit=0)
    assert (await deny_all.usage_summary(deny_customer.id))["llm_tokens"]["limit"] == 0


@pytest.mark.asyncio
async def test_summary_is_empty_without_a_subscription():
    repository = InMemoryCommercialRepository()
    control_plane = CommercialControlPlane(repository)
    customer = await control_plane.ensure_billing_customer(owner_id="u-none")
    summary = await control_plane.usage_summary(customer.id)
    assert summary == {}


@pytest.mark.asyncio
async def test_summary_window_matches_subscription_window():
    control_plane, customer = await _metered_plane(window="subscription")
    summary = await control_plane.usage_summary(customer.id)
    entry = summary["llm_tokens"]
    assert entry["window"] == "subscription"
    # Subscription windows span the whole trial period, not just today.
    assert (entry["period_end"] - entry["period_start"]).days >= 6
