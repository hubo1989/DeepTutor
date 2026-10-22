// Unit tests for the per-user quota form helpers (features/multi-user/quota-ui.ts).
// The descriptor drives the admin GrantEditor quota section, so these tests pin
// field coverage and the parse/serialize contract the PUT /grants payload relies on.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  QUOTA_FIELD_GROUPS,
  QUOTA_FORM_FIELDS,
  LLM_TOKEN_UNIT,
  EMBEDDING_TOKEN_UNIT,
  parseQuotaInput,
  parseQuotaInputs,
  quotaInputValue,
} from '../features/multi-user/quota-ui'

const FALLBACK = {
  llm: { daily_tokens: 100_000, monthly_tokens: 1_000_000 },
  embedding: { daily_tokens: 1_000_000, monthly_tokens: 10_000_000 },
  mineru: { daily_pages: 50, monthly_pages: 500, max_pages_per_file: 50 },
}

test('QUOTA_FORM_FIELDS covers every persisted quota field exactly once', () => {
  const keys = QUOTA_FORM_FIELDS.map(field => field.key)
  assert.deepEqual([...new Set(keys)].sort(), [
    'embedding.daily_tokens',
    'embedding.monthly_tokens',
    'llm.daily_tokens',
    'llm.monthly_tokens',
    'mineru.daily_pages',
    'mineru.max_pages_per_file',
    'mineru.monthly_pages',
  ])
  // Every group's rows must be reflected in the flattened list.
  assert.equal(
    QUOTA_FIELD_GROUPS.reduce((total, group) => total + group.rows.length, 0),
    QUOTA_FORM_FIELDS.length
  )
})

test('parseQuotaInputs applies units and keeps fallback for untouched fields', () => {
  const quota = parseQuotaInputs({ 'llm.daily_tokens': '50', 'mineru.daily_pages': '0' }, FALLBACK)
  assert.ok(quota)
  assert.equal(quota.llm.daily_tokens, 50 * LLM_TOKEN_UNIT)
  assert.equal(quota.llm.monthly_tokens, FALLBACK.llm.monthly_tokens)
  assert.equal(quota.mineru.daily_pages, 0) // 0 = unlimited, not an error
  assert.equal(quota.mineru.monthly_pages, FALLBACK.mineru.monthly_pages)
  assert.equal(quota.embedding.daily_tokens, FALLBACK.embedding.daily_tokens)
})

test('parseQuotaInputs rejects invalid or overflowing input wholesale', () => {
  assert.equal(parseQuotaInputs({ 'llm.daily_tokens': 'abc' }, FALLBACK), null)
  assert.equal(parseQuotaInputs({ 'llm.daily_tokens': '-1' }, FALLBACK), null)
  assert.equal(parseQuotaInputs({ 'llm.daily_tokens': `${10_000_000_001}` }, FALLBACK), null)
  // Embedding inputs scale by their own unit, not the LLM unit.
  const embedding = parseQuotaInputs({ 'embedding.daily_tokens': '3' }, FALLBACK)
  assert.ok(embedding)
  assert.equal(embedding.embedding.daily_tokens, 3 * EMBEDDING_TOKEN_UNIT)
})

test('quotaInputValue round-trips parsed display values', () => {
  assert.equal(quotaInputValue(500_000, LLM_TOKEN_UNIT), '50')
  assert.equal(quotaInputValue(0, LLM_TOKEN_UNIT), '0')
  // Non-integer unit splits keep up to four decimals, matching the parser.
  const shown = quotaInputValue(15_000, LLM_TOKEN_UNIT)
  assert.equal(parseQuotaInput(shown, LLM_TOKEN_UNIT), 15_000)
})
