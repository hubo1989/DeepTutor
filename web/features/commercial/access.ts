import type { Capability } from "@/lib/capability-routes";

import {
  COMMERCIAL_STATUSES,
  type CommercialAccessSnapshot,
  type CommercialStatus,
} from "./types";

const ACTIVE_STATUSES = new Set<CommercialStatus>([
  "trialing",
  "active",
]);
const MAX_TIMER_DELAY_MS = 2_147_000_000;
const EXPIRED_REFRESH_DELAY_MS = 60_000;

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isCommercialStatus(value: unknown): value is CommercialStatus {
  return (
    value === null ||
    (typeof value === "string" &&
      (COMMERCIAL_STATUSES as readonly string[]).includes(value))
  );
}

/**
 * Validate the public API contract before it can influence route access.
 * A malformed response returns null, which intentionally leaves the UI open;
 * the backend remains the enforcement boundary.
 */
export function parseCommercialAccessSnapshot(
  value: unknown,
): CommercialAccessSnapshot | null {
  if (!isPlainRecord(value)) return null;
  if (typeof value.enabled !== "boolean") return null;
  if (typeof value.is_admin !== "boolean") return null;
  if (!isCommercialStatus(value.status)) return null;
  if (
    value.valid_until !== null &&
    (typeof value.valid_until !== "string" ||
      !Number.isFinite(Date.parse(value.valid_until)))
  ) {
    return null;
  }
  if (
    value.plan_version_id !== null &&
    typeof value.plan_version_id !== "string"
  ) {
    return null;
  }
  if (!isPlainRecord(value.values)) return null;

  return {
    enabled: value.enabled,
    isAdmin: value.is_admin,
    status: value.status,
    validUntil: value.valid_until,
    planVersionId: value.plan_version_id,
    values: value.values,
  };
}

/** Whether the snapshot grants access at this instant. */
export function hasEffectiveCommercialAccess(
  snapshot: CommercialAccessSnapshot,
  nowMs = Date.now(),
): boolean {
  if (!snapshot.enabled || snapshot.isAdmin) return true;
  if (!ACTIVE_STATUSES.has(snapshot.status)) return false;

  // An active status without a client-readable deadline remains usable. This
  // avoids a bad/mid-deploy payload causing a false lock; the backend still
  // authorizes every costly operation.
  if (!snapshot.validUntil) return true;
  return Date.parse(snapshot.validUntil) > nowMs;
}

/** Only costly feature routes are replaced by the expired-trial notice. */
export function shouldBlockCommercialRoute(
  snapshot: CommercialAccessSnapshot | null,
  capability: Capability | null,
  nowMs = Date.now(),
): boolean {
  if (!capability || !snapshot) return false;
  return !hasEffectiveCommercialAccess(snapshot, nowMs);
}

/** Delay until the provider should re-resolve access at the server. */
export function expiryRefreshDelayMs(
  snapshot: CommercialAccessSnapshot | null,
  nowMs = Date.now(),
): number | null {
  if (
    !snapshot ||
    !snapshot.enabled ||
    snapshot.isAdmin ||
    !ACTIVE_STATUSES.has(snapshot.status) ||
    !snapshot.validUntil
  ) {
    return null;
  }

  const remaining = Date.parse(snapshot.validUntil) - nowMs;
  if (!Number.isFinite(remaining)) return null;
  if (remaining <= 0) return EXPIRED_REFRESH_DELAY_MS;
  return Math.min(Math.max(remaining + 100, 250), MAX_TIMER_DELAY_MS);
}

export type TrialCountdown = {
  days: number;
  hours: number;
  minutes: number;
  expired: boolean;
};

export function trialCountdown(
  validUntil: string | null,
  nowMs = Date.now(),
): TrialCountdown | null {
  if (!validUntil) return null;
  const deadline = Date.parse(validUntil);
  if (!Number.isFinite(deadline)) return null;

  const remainingMs = Math.max(0, deadline - nowMs);
  const totalMinutes = Math.ceil(remainingMs / 60_000);
  return {
    days: Math.floor(totalMinutes / (24 * 60)),
    hours: Math.floor((totalMinutes % (24 * 60)) / 60),
    minutes: totalMinutes % 60,
    expired: remainingMs <= 0,
  };
}
