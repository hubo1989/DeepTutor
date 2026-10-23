export const COMMERCIAL_STATUSES = [
  'disabled',
  'admin',
  'trialing',
  'active',
  'past_due',
  'canceled',
  'expired',
] as const

export type CommercialStatus = (typeof COMMERCIAL_STATUSES)[number] | null

/** One quota meter's consumption inside its current window. */
export type CommercialMeterUsage = {
  limit: number | null
  window: 'utc_day' | 'subscription'
  used: number
  reserved: number
}

/** Public, credential-free commercial state returned for the signed-in user. */
export type CommercialAccessSnapshot = {
  enabled: boolean
  isAdmin: boolean
  status: CommercialStatus
  validUntil: string | null
  planVersionId: string | null
  values: Record<string, unknown>
  /** Per-meter usage; absent on older payloads. */
  usage: Record<string, CommercialMeterUsage>
}
