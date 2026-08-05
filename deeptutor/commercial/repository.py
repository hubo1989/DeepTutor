"""Persistence boundary for the commercial control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    BillingCustomer,
    Entitlement,
    PlanVersion,
    ReconciliationReport,
    Subscription,
    SubscriptionStatus,
    UsageEvent,
    UsageReservation,
    WebhookEvent,
)


@runtime_checkable
class CommercialRepository(Protocol):
    async def ensure_billing_customer(self, candidate: BillingCustomer) -> BillingCustomer: ...

    async def get_billing_customer(self, customer_id: str) -> BillingCustomer | None: ...

    async def get_billing_customer_by_owner(self, owner_id: str) -> BillingCustomer | None: ...

    async def publish_plan_version(
        self,
        candidate: PlanVersion,
        entitlements: tuple[Entitlement, ...],
    ) -> PlanVersion: ...

    async def get_plan_version(self, plan_version_id: str) -> PlanVersion | None: ...

    async def list_entitlements(self, plan_version_id: str) -> tuple[Entitlement, ...]: ...

    async def ensure_trial(
        self,
        candidate: Subscription,
        claimed_at: datetime,
    ) -> Subscription: ...

    async def create_subscription(self, candidate: Subscription) -> Subscription: ...

    async def activate_paid_subscription(
        self,
        candidate: Subscription,
        changed_at: datetime,
    ) -> Subscription: ...

    async def get_subscription(self, subscription_id: str) -> Subscription | None: ...

    async def list_subscriptions(self, customer_id: str) -> tuple[Subscription, ...]: ...

    async def transition_subscription(
        self,
        subscription_id: str,
        target: SubscriptionStatus,
        changed_at: datetime,
    ) -> Subscription: ...

    async def get_entitled_subscription(
        self,
        customer_id: str,
        at: datetime,
    ) -> Subscription | None: ...

    async def record_webhook_event(self, candidate: WebhookEvent) -> WebhookEvent: ...

    async def mark_webhook_processed(
        self,
        event_id: str,
        processed_at: datetime,
        processing_error: str | None = None,
    ) -> WebhookEvent: ...

    async def get_usage_reservation_by_dedupe_key(
        self,
        dedupe_key: str,
    ) -> UsageReservation | None: ...

    async def reserve_usage(
        self,
        candidate: UsageReservation,
        quota_limit: int | None,
    ) -> UsageReservation: ...

    async def get_usage_reservation(
        self,
        reservation_id: str,
    ) -> UsageReservation | None: ...

    async def list_usage_reservations(
        self,
        customer_id: str,
    ) -> tuple[UsageReservation, ...]: ...

    async def list_usage_events(self, customer_id: str) -> tuple[UsageEvent, ...]: ...

    async def finalize_usage(
        self,
        reservation_id: str,
        actual_quantity: int,
        event_candidate: UsageEvent,
        finalized_at: datetime,
    ) -> UsageEvent: ...

    async def release_usage(
        self,
        reservation_id: str,
        released_at: datetime,
    ) -> UsageReservation: ...

    async def reconcile(self, at: datetime, *, repair: bool) -> ReconciliationReport: ...

    async def erase_customer(self, owner_id: str) -> bool: ...
