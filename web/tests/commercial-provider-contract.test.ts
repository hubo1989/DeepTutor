import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

const webRoot = process.cwd();
const read = (relativePath: string) =>
  readFileSync(path.resolve(webRoot, relativePath), "utf8");

test("commercial provider refreshes on focus, visibility, and known expiry", () => {
  const source = read("features/commercial/CommercialAccessContext.tsx");
  assert.match(source, /fetchCommercialAccess\(\)/);
  assert.match(source, /addEventListener\("focus", onFocus\)/);
  assert.match(source, /addEventListener\("visibilitychange", onVisible\)/);
  assert.match(source, /expiryRefreshDelayMs\(snapshot\)/);
  assert.match(source, /setTimeout\(\(\) => void refresh\(\), delay\)/);
});

test("both signed-in app shells mount the commercial provider and trial banner", () => {
  for (const layout of [
    "app/(workspace)/layout.tsx",
    "app/(utility)/layout.tsx",
  ]) {
    const source = read(layout);
    assert.match(source, /<CommercialAccessProvider>/);
    assert.match(source, /<TrialStatusBanner \/>/);
  }
});

test("route gate gives commercial expiry precedence only after capability mapping", () => {
  const source = read("components/access/CapabilityGate.tsx");
  assert.match(source, /const capability = capabilityForPath\(pathname\)/);
  assert.match(
    source,
    /shouldBlockCommercialRoute\(snapshot, capability\)/,
  );
  assert.match(
    source,
    /return <TrialExpiredNotice status=\{snapshot\?\.status \?\? null\} \/>/,
  );
  assert.match(source, /<RequireCapability capability=\{capability\}>/);
});

test("trial UI contains no checkout or purchase action", () => {
  const source = [
    read("features/commercial/TrialStatusBanner.tsx"),
    read("features/commercial/TrialExpiredNotice.tsx"),
  ].join("\n");
  assert.doesNotMatch(source, /href=["']\/checkout/i);
  assert.doesNotMatch(source, /createCheckout|checkoutSession|purchasePlan/);
});
