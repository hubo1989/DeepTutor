import type { ResourceQuota } from '@/lib/admin-api'

export const QUOTA_MAX_UNITS = 10_000_000_000
export const LLM_TOKEN_UNIT = 10_000
export const EMBEDDING_TOKEN_UNIT = 1_000_000
export const MINERU_PAGE_UNIT = 1

/** One editable quota limit inside a resource block, e.g. llm.daily_tokens. */
export type QuotaFieldKey =
  | 'llm.daily_tokens'
  | 'llm.monthly_tokens'
  | 'embedding.daily_tokens'
  | 'embedding.monthly_tokens'
  | 'mineru.daily_pages'
  | 'mineru.monthly_pages'
  | 'mineru.max_pages_per_file'

type QuotaRowLabelKey = 'Daily limit' | 'Monthly limit' | 'Per-file limit'

export type QuotaFormRow = {
  key: QuotaFieldKey
  /** Display unit the raw value is divided by for editing. */
  unit: number
  /** i18n label key shown next to the input. */
  labelKey: QuotaRowLabelKey
}

export type QuotaFormGroup = {
  /** Resource block key in the persisted grant. */
  resource: 'llm' | 'embedding' | 'mineru'
  /** i18n heading key for the group. */
  labelKey: 'LLM tokens' | 'Embedding tokens' | 'Document parsing pages'
  rows: QuotaFormRow[]
}

/** Render order + units for the per-user quota editor. */
export const QUOTA_FIELD_GROUPS: readonly QuotaFormGroup[] = [
  {
    resource: 'llm',
    labelKey: 'LLM tokens',
    rows: [
      { key: 'llm.daily_tokens', unit: LLM_TOKEN_UNIT, labelKey: 'Daily limit' },
      { key: 'llm.monthly_tokens', unit: LLM_TOKEN_UNIT, labelKey: 'Monthly limit' },
    ],
  },
  {
    resource: 'embedding',
    labelKey: 'Embedding tokens',
    rows: [
      { key: 'embedding.daily_tokens', unit: EMBEDDING_TOKEN_UNIT, labelKey: 'Daily limit' },
      { key: 'embedding.monthly_tokens', unit: EMBEDDING_TOKEN_UNIT, labelKey: 'Monthly limit' },
    ],
  },
  {
    resource: 'mineru',
    labelKey: 'Document parsing pages',
    rows: [
      { key: 'mineru.daily_pages', unit: MINERU_PAGE_UNIT, labelKey: 'Daily limit' },
      { key: 'mineru.monthly_pages', unit: MINERU_PAGE_UNIT, labelKey: 'Monthly limit' },
      { key: 'mineru.max_pages_per_file', unit: MINERU_PAGE_UNIT, labelKey: 'Per-file limit' },
    ],
  },
]

/** Every editable field, flattened. */
export const QUOTA_FORM_FIELDS: readonly {
  key: QuotaFieldKey
  unit: number
  resource: QuotaFormGroup['resource']
  field: string
}[] = QUOTA_FIELD_GROUPS.flatMap(group =>
  group.rows.map(row => ({
    key: row.key,
    unit: row.unit,
    resource: group.resource,
    field: row.key.slice(row.key.indexOf('.') + 1),
  }))
)

export function quotaInputValue(raw: number, unit: number): string {
  if (!Number.isFinite(raw) || raw <= 0) return '0'
  const value = raw / unit
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
}

export function parseQuotaInput(value: string, unit: number): number | null {
  const trimmed = value.trim()
  if (!/^\d+(?:\.\d{1,4})?$/.test(trimmed)) return null
  const parsed = Number(trimmed) * unit
  if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > QUOTA_MAX_UNITS) {
    return null
  }
  return parsed
}

/**
 * Parse a full admin-quota form ({ "llm.daily_tokens": "50", ... }) into a
 * grant-ready ResourceQuota. Returns null when any value is invalid, so the
 * caller refuses to save instead of silently dropping fields.
 */
export function parseQuotaInputs(
  inputs: Record<string, string>,
  fallback: ResourceQuota
): ResourceQuota | null {
  const quota: ResourceQuota = {
    llm: { ...fallback.llm },
    embedding: { ...fallback.embedding },
    mineru: { ...fallback.mineru },
  }
  for (const field of QUOTA_FORM_FIELDS) {
    const raw = inputs[field.key]
    if (raw === undefined) continue
    const parsed = parseQuotaInput(raw, field.unit)
    if (parsed === null) return null
    // The descriptor guarantees resource/field name a known quota member, so
    // the double cast is just the index write the discriminated types cannot
    // express directly.
    ;(quota[field.resource] as unknown as Record<string, number>)[field.field] = parsed
  }
  return quota
}

export function formatQuotaRaw(raw: number, suffix: string): string {
  return `${new Intl.NumberFormat('en-US').format(raw)} ${suffix}`
}
