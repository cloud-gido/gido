/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  buildLocalPipelineExplain,
  redactPipelineText,
  sanitizePipelineArtifact,
} from './streamPipeline'

describe('stream pipeline artifact safety', () => {
  it('redacts SQL options and credentials embedded in URLs', () => {
    const text = "'password' = 'top-secret'\nurl=mysql://alice:hunter2@db/orders"
    const output = redactPipelineText(text)
    expect(output).not.toContain('top-secret')
    expect(output).not.toContain('hunter2')
    expect(output).toContain('******')
  })

  it('deep-redacts sensitive response fields', () => {
    const value = sanitizePipelineArtifact({
      source: { password: 'plain', nested: [{ access_key: 'ak', table: 'orders' }] },
    })
    expect(value.source.password).toBe('******')
    expect(value.source.nested[0].access_key).toBe('******')
    expect(value.source.nested[0].table).toBe('orders')
  })

  it('marks upsert without a primary key as blocking', () => {
    const result = buildLocalPipelineExplain({
      mode: 'upsert',
      source: { table: 'orders', credential_ref: 'mysql-prod' },
      sink: { database: 'ods', table: 'orders', primary_keys: [] },
      schema: { columns: [] },
    })
    expect(result.valid).toBe(false)
    expect(result.risks.some(risk => risk.code === 'PRIMARY_KEY_REQUIRED')).toBe(true)
    expect(result.generated_artifact.redacted).toBe(true)
  })
})
