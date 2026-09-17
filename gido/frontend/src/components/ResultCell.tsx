/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useMemo, useState } from 'react'
import { Button, Drawer, Segmented, Tag, message } from 'antd'
import { CopyOutlined, ExpandOutlined } from '@ant-design/icons'
import { formatCellDisplay } from '../utils/cellDisplay'

const JSON_TYPES = /\b(json|array|map|struct|object)\b/i
const BOOL_TYPES = /\b(bool|boolean)\b/i
const INTEGER_TYPES = /\b(tinyint|smallint|integer|bigint|int\d*)\b/i
const DECIMAL_TYPES = /\b(number|decimal|numeric|float|double|real)\b/i
const DATE_TYPES = /\bdate\b/i
const DATETIME_TYPES = /\b(datetime|timestamp|time)\b/i
const BINARY_TYPES = /\b(binary|varbinary|blob|bytea)\b/i

function serialize(value: unknown): string {
  if (typeof value === 'string') return value
  if (value instanceof Uint8Array) return Array.from(value).map(v => v.toString(16).padStart(2, '0')).join(' ')
  return formatCellDisplay(value, 0)
}

function parsedJson(value: unknown): unknown {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function binarySize(value: unknown, fallback: string): number {
  if (value instanceof Uint8Array) return value.byteLength
  if (typeof value === 'string' && value.startsWith('base64:')) {
    try {
      return Math.floor(window.atob(value.slice(7)).length)
    } catch {
      return fallback.length
    }
  }
  return fallback.length
}

export default function ResultCell({ value, type }: { value: unknown; type?: string | null }) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'formatted' | 'raw'>('formatted')
  const raw = useMemo(() => serialize(value), [value])
  const structured = Array.isArray(value) || (!!value && typeof value === 'object') || JSON_TYPES.test(type || '')
  const longText = raw.length > 120 || raw.includes('\n')
  const expandable = structured || longText
  const formatted = useMemo(() => {
    if (!structured) return raw
    try {
      return JSON.stringify(parsedJson(value), null, 2)
    } catch {
      return raw
    }
  }, [raw, structured, value])

  if (value === null || value === undefined) {
    return <span className="dw-result-cell dw-result-cell--null">NULL</span>
  }

  let preview
  if (BOOL_TYPES.test(type || '') || typeof value === 'boolean') {
    const truthy = value === true || String(value).toLowerCase() === 'true' || value === 1
    preview = <Tag color={truthy ? 'green' : 'default'}>{truthy ? 'TRUE' : 'FALSE'}</Tag>
  } else if (BINARY_TYPES.test(type || '') || value instanceof Uint8Array) {
    preview = <span className="dw-result-cell--binary">BINARY · {binarySize(value, raw)} bytes</span>
  } else {
    const kind = INTEGER_TYPES.test(type || '') ? 'integer'
      : DECIMAL_TYPES.test(type || '') ? 'decimal'
        : DATETIME_TYPES.test(type || '') ? 'datetime'
          : DATE_TYPES.test(type || '') ? 'date'
            : structured ? 'json' : 'text'
    preview = <span className={`dw-result-cell--${kind}`}>{raw}</span>
  }

  return (
    <>
      <span className="dw-result-cell">
        <span className="dw-result-cell__preview" title={raw.length > 80 ? raw : undefined}>{preview}</span>
        {expandable && (
          <Button
            type="text"
            size="small"
            className="dw-result-cell__expand"
            icon={<ExpandOutlined />}
            aria-label="展开单元格内容"
            onClick={event => {
              event.stopPropagation()
              setOpen(true)
            }}
          />
        )}
      </span>
      <Drawer
        title={structured ? '结构化数据' : '长文本'}
        width={640}
        open={open}
        onClose={() => setOpen(false)}
        extra={(
          <Button
            icon={<CopyOutlined />}
            onClick={() => navigator.clipboard.writeText(mode === 'formatted' ? formatted : raw)
              .then(() => message.success('内容已复制'))
              .catch(() => message.error('复制失败'))}
          >
            复制
          </Button>
        )}
      >
        <Segmented
          value={mode}
          options={[
            { label: '格式化', value: 'formatted' },
            { label: '原文', value: 'raw' },
          ]}
          onChange={value => setMode(value as 'formatted' | 'raw')}
          style={{ marginBottom: 12 }}
        />
        <pre className="dw-result-cell__drawer-content">{mode === 'formatted' ? formatted : raw}</pre>
      </Drawer>
    </>
  )
}
