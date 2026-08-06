import test from "node:test";
import assert from "node:assert/strict";

import {
  expiryRefreshDelayMs,
  hasEffectiveCommercialAccess,
  parseCommercialAccessSnapshot,
  shouldBlockCommercialRoute,
  trialCountdown,
} from "../features/commercial/access";
import type { CommercialAccessSnapshot } from "../features/commercial/types";

const NOW = Date.parse("2026-08-05T08:00:00.000Z");

function snapshot(
  overrides: Partial<CommercialAccessSnapshot> = {},
): CommercialAccessSnapshot {
  return {
    enabled: true,
    isAdmin: false,
    status: "trialing",
    validUntil: "2026-08-06T10:30:00.000Z",
    planVersionId: "trial_v1",
    values: {},
    ...overrides,
  };
}

test("commercial API parser accepts the stable public schema", () => {
  assert.deepEqual(
    parseCommercialAccessSnapshot({
      enabled: true,
      is_admin: false,
      status: "trialing",
      valid_until: "2026-08-12T08:00:00+00:00",
      plan_version_id: "trial_v1",
      values: { "quota.llm_tokens": { limit: 100_000 } },
    }),
    {
      enabled: true,
      isAdmin: false,
      status: "trialing",
      validUntil: "2026-08-12T08:00:00+00:00",
      planVersionId: "trial_v1",
      values: { "quota.llm_tokens": { limit: 100_000 } },
    },
  );
});

test("commercial API parser fails open on malformed or unknown data", () => {
  assert.equal(parseCommercialAccessSnapshot(null), null);
  assert.equal(
    parseCommercialAccessSnapshot({
      enabled: true,
      is_admin: false,
      status: "mystery",
      valid_until: null,
      plan_version_id: null,
      values: {},
    }),
    null,
  );
  assert.equal(
    parseCommercialAccessSnapshot({
      enabled: true,
      is_admin: false,
      status: "trialing",
      valid_until: "not-a-date",
      plan_version_id: null,
      values: {},
    }),
    null,
  );
});

test("disabled mode and admins bypass commercial gating", () => {
  assert.equal(
    shouldBlockCommercialRoute(snapshot({ enabled: false }), "llm", NOW),
    false,
  );
  assert.equal(
    shouldBlockCommercialRoute(snapshot({ isAdmin: true }), "llm", NOW),
    false,
  );
});

test("only costly capability routes lock after trial expiry", () => {
  const expired = snapshot({
    status: "expired",
    validUntil: "2026-08-05T07:59:59.000Z",
  });
  assert.equal(shouldBlockCommercialRoute(expired, "llm", NOW), true);
  assert.equal(shouldBlockCommercialRoute(expired, null, NOW), false);
  assert.equal(shouldBlockCommercialRoute(null, "llm", NOW), false);
});

test("past-due, canceled, and missing subscriptions cannot consume", () => {
  assert.equal(
    hasEffectiveCommercialAccess(snapshot({ status: "canceled" }), NOW),
    false,
  );
  assert.equal(
    hasEffectiveCommercialAccess(snapshot({ status: null }), NOW),
    false,
  );
  assert.equal(
    hasEffectiveCommercialAccess(snapshot({ status: "past_due" }), NOW),
    false,
  );
  assert.equal(
    shouldBlockCommercialRoute(snapshot({ status: "past_due" }), "llm", NOW),
    true,
  );
  assert.equal(
    shouldBlockCommercialRoute(snapshot({ status: "past_due" }), null, NOW),
    false,
  );
});

test("provider expiry timer targets the known deadline", () => {
  assert.equal(expiryRefreshDelayMs(snapshot(), NOW), 95_400_100);
  assert.equal(
    expiryRefreshDelayMs(snapshot({ status: "expired" }), NOW),
    null,
  );
  assert.equal(
    expiryRefreshDelayMs(
      snapshot({ validUntil: "2026-08-05T07:59:59.000Z" }),
      NOW,
    ),
    60_000,
  );
});

test("trial countdown rounds up to avoid showing zero before expiry", () => {
  assert.deepEqual(trialCountdown("2026-08-06T10:30:00.000Z", NOW), {
    days: 1,
    hours: 2,
    minutes: 30,
    expired: false,
  });
  assert.deepEqual(trialCountdown("2026-08-05T08:00:01.000Z", NOW), {
    days: 0,
    hours: 0,
    minutes: 1,
    expired: false,
  });
  assert.equal(trialCountdown(null, NOW), null);
});
