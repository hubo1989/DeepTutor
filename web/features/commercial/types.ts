export const COMMERCIAL_STATUSES = [
  "disabled",
  "admin",
  "trialing",
  "active",
  "past_due",
  "canceled",
  "expired",
] as const;

export type CommercialStatus = (typeof COMMERCIAL_STATUSES)[number] | null;

/** Public, credential-free commercial state returned for the signed-in user. */
export type CommercialAccessSnapshot = {
  enabled: boolean;
  isAdmin: boolean;
  status: CommercialStatus;
  validUntil: string | null;
  planVersionId: string | null;
  values: Record<string, unknown>;
};
