"use client";

import { usePathname } from "next/navigation";

import { capabilityForPath } from "@/lib/capability-routes";
import {
  TrialExpiredNotice,
  useCommercialAccess,
} from "@/features/commercial";
import { shouldBlockCommercialRoute } from "@/features/commercial/access";

import { RequireCapability } from "./RequireCapability";

/**
 * Route-level gate: derives the required model capability from the current
 * pathname and locks the page when the user lacks it. Mounted once per authed
 * layout group, so direct-URL access to a gated feature is covered too.
 */
export default function CapabilityGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const capability = capabilityForPath(pathname);
  const { snapshot } = useCommercialAccess();

  // Commercial expiry is a different condition from an administrator model
  // grant. Explain it first, but only on routes that actually consume a model.
  // An unresolved/failed commercial probe is deliberately fail-open here.
  if (shouldBlockCommercialRoute(snapshot, capability)) {
    return <TrialExpiredNotice status={snapshot?.status ?? null} />;
  }

  return (
    <RequireCapability capability={capability}>{children}</RequireCapability>
  );
}
