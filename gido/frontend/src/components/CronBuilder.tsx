/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef } from 'react'
import { Tabs, InputNumber, Radio, Space, Tag, Input } from 'antd'
import { schedulerApi } from '../api'

interface CronBuilderProps {
  value?: string
  onChange?: (cron: string) => void
}

// 分钟/小时/日/月/周 各字段的配置
type FieldMode = 'every' | 'interval' | 'specific' | 'range'

interface FieldState {
  mode: FieldMode
  interval: number      // 每隔N
  intervalStart: number // 从第N开始
  specific: number[]    // 指定值
  rangeStart: number
  rangeEnd: number
}

const defaultField = (interval = 1, intervalStart = 0): FieldState => ({
  mode: 'every', interval, intervalStart, specific: [], rangeStart: 0, rangeEnd: 1
})

/** 将 5 段 Linux cron 同步到可视化字段（用于快捷预设，避免只改表单值而底部预览仍用旧 buildCron） */
function fieldStatesFromFivePartCron(cron: string): {
  minute: FieldState
  hour: FieldState
  day: FieldState
  month: FieldState
  week: FieldState
} | null {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [a, b, c, d, e] = parts
  const every = () => ({ ...defaultField(1, 0), mode: 'every' as const })
  const specific0 = (n: number): FieldState => ({
    ...defaultField(1, 0),
    mode: 'specific',
    specific: [n],
  })
  const parsePart = (s: string, min: number, max: number): FieldState => {
    if (s === '*') return every()
    if (/^\d+$/.test(s)) {
      const n = parseInt(s, 10)
      if (n >= min && n <= max) return specific0(n)
    }
    if (s.includes(',')) {
      const xs = s.split(',').map(x => parseInt(x.trim(), 10)).filter(n => !Number.isNaN(n) && n >= min && n <= max)
      if (xs.length) return { ...defaultField(1, 0), mode: 'specific', specific: xs }
    }
    return every()
  }
  return {
    minute: parsePart(a, 0, 59),
    hour: parsePart(b, 0, 23),
    day: parsePart(c, 1, 31),
    month: parsePart(d, 1, 12),
    week: parsePart(e, 0, 6),
  }
}

const WEEK_LABELS = ['日', '一', '二', '三', '四', '五', '六']
const MONTH_LABELS = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']

function buildExpr(f: FieldState, _min: number, _max: number): string {
  switch (f.mode) {
    case 'every': return '*'
    case 'interval': return `${f.intervalStart}/${f.interval}`
    case 'specific': return f.specific.length ? f.specific.sort((a,b)=>a-b).join(',') : '*'
    case 'range': return `${f.rangeStart}-${f.rangeEnd}`
    default: return '*'
  }
}

function FieldPanel({
  label, field, onChange, min, max, labels
}: {
  label: string
  field: FieldState
  onChange: (f: FieldState) => void
  min: number
  max: number
  labels?: string[]
}) {
  const nums = Array.from({ length: max - min + 1 }, (_, i) => i + min)

  return (
    <div style={{ padding: '8px 0' }}>
      <Radio.Group
        value={field.mode}
        onChange={e => onChange({ ...field, mode: e.target.value })}
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        <Radio value="every">每{label}（*）</Radio>

        <Radio value="interval">
          <Space>
            从第
            <InputNumber
              min={min} max={max} size="small" style={{ width: 60 }}
              value={field.intervalStart}
              onChange={v => onChange({ ...field, intervalStart: v ?? min })}
              disabled={field.mode !== 'interval'}
            />
            {label}开始，每隔
            <InputNumber
              min={1} max={max} size="small" style={{ width: 60 }}
              value={field.interval}
              onChange={v => onChange({ ...field, interval: v ?? 1 })}
              disabled={field.mode !== 'interval'}
            />
            {label}执行一次
          </Space>
        </Radio>

        <Radio value="range">
          <Space>
            从
            <InputNumber
              min={min} max={max} size="small" style={{ width: 60 }}
              value={field.rangeStart}
              onChange={v => onChange({ ...field, rangeStart: v ?? min })}
              disabled={field.mode !== 'range'}
            />
            到
            <InputNumber
              min={min} max={max} size="small" style={{ width: 60 }}
              value={field.rangeEnd}
              onChange={v => onChange({ ...field, rangeEnd: v ?? max })}
              disabled={field.mode !== 'range'}
            />
            {label}
          </Space>
        </Radio>

        <Radio value="specific">
          指定{label}
          <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {nums.map(n => (
              <Tag
                key={n}
                color={field.specific.includes(n) ? 'blue' : 'default'}
                style={{ cursor: field.mode === 'specific' ? 'pointer' : 'not-allowed', userSelect: 'none', minWidth: 32, textAlign: 'center' }}
                onClick={() => {
                  if (field.mode !== 'specific') return
                  const s = field.specific.includes(n)
                    ? field.specific.filter(x => x !== n)
                    : [...field.specific, n]
                  onChange({ ...field, specific: s })
                }}
              >
                {labels ? labels[n - min] : n}
              </Tag>
            ))}
          </div>
        </Radio>
      </Radio.Group>
    </div>
  )
}

export default function CronBuilder({ value, onChange }: CronBuilderProps) {
  const [minute, setMinute] = useState<FieldState>(defaultField(1, 0))
  const [hour, setHour] = useState<FieldState>(defaultField(1, 0))
  const [day, setDay] = useState<FieldState>(defaultField(1, 1))
  const [month, setMonth] = useState<FieldState>(defaultField(1, 1))
  const [week, setWeek] = useState<FieldState>({ ...defaultField(1, 0), mode: 'every' })
  const [manualCron, setManualCron] = useState('')
  const [mode, setMode] = useState<'visual' | 'manual'>('visual')
  const [nextTimes, setNextTimes] = useState<string[]>([])
  const [quartzCron, setQuartzCron] = useState('')
  const [previewError, setPreviewError] = useState('')
  const previewSeq = useRef(0)

  // 解析传入的 cron 值
  useEffect(() => {
    if (!value) return
    setManualCron(value)
  }, [value])

  const buildCron = (m: FieldState, h: FieldState, d: FieldState, mo: FieldState, w: FieldState) => {
    const parts = [
      buildExpr(m, 0, 59),
      buildExpr(h, 0, 23),
      buildExpr(d, 1, 31),
      buildExpr(mo, 1, 12),
      buildExpr(w, 0, 6),
    ]
    return parts.join(' ')
  }

  const handleFieldChange = (
    setter: (f: FieldState) => void,
    newField: FieldState,
    which: 'minute' | 'hour' | 'day' | 'month' | 'week'
  ) => {
    setter(newField)
    const fields = { minute, hour, day, month, week, [which]: newField }
    const cron = buildCron(fields.minute, fields.hour, fields.day, fields.month, fields.week)
    onChange?.(cron)
    setManualCron(cron)
  }

  const builtVisual = buildCron(minute, hour, day, month, week)
  // 可视化下：表单 value（含点预设后父组件回传）应与底部预览一致；避免「点了每分钟下面还是旧 cron」
  const currentCron =
    mode === 'manual'
      ? manualCron
      : (value != null && String(value).trim() !== '' ? String(value).trim() : builtVisual)

  // 类似 DolphinScheduler：预览最近几次执行时间（Asia/Shanghai）
  useEffect(() => {
    const cron = (currentCron || '').trim()
    if (!cron || cron.split(/\s+/).length !== 5) {
      setNextTimes([])
      setQuartzCron('')
      setPreviewError(cron ? '须为 5 段：分 时 日 月 周' : '')
      return
    }
    const seq = ++previewSeq.current
    const timer = window.setTimeout(() => {
      schedulerApi.previewCron(cron, 5)
        .then((res: any) => {
          if (seq !== previewSeq.current) return
          const d = res?.next_times != null ? res : (res?.data ?? res)
          setNextTimes(Array.isArray(d?.next_times) ? d.next_times : [])
          setQuartzCron(typeof d?.quartz_cron === 'string' ? d.quartz_cron : '')
          setPreviewError('')
        })
        .catch((err: any) => {
          if (seq !== previewSeq.current) return
          setNextTimes([])
          setQuartzCron('')
          const msg = err?.response?.data?.detail || err?.message || '预览失败'
          setPreviewError(typeof msg === 'string' ? msg : '预览失败')
        })
    }, 280)
    return () => window.clearTimeout(timer)
  }, [currentCron])

  // 常用预设
  const PRESETS = [
    { label: '每分钟', cron: '* * * * *' },
    { label: '每小时', cron: '0 * * * *' },
    { label: '每天0点', cron: '0 0 * * *' },
    { label: '每天1点', cron: '0 1 * * *' },
    { label: '每天8点', cron: '0 8 * * *' },
    { label: '每周一0点', cron: '0 0 * * 1' },
    { label: '每月1号', cron: '0 0 1 * *' },
  ]

  return (
    <div>
      {/* 预设快捷选择 */}
      <div style={{ marginBottom: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {PRESETS.map(p => (
          <Tag
            key={p.cron}
            color={currentCron === p.cron ? 'blue' : 'default'}
            style={{ cursor: 'pointer' }}
            onClick={() => {
              const synced = fieldStatesFromFivePartCron(p.cron)
              if (synced) {
                setMinute(synced.minute)
                setHour(synced.hour)
                setDay(synced.day)
                setMonth(synced.month)
                setWeek(synced.week)
              }
              setManualCron(p.cron)
              onChange?.(p.cron)
            }}
          >
            {p.label}
          </Tag>
        ))}
      </div>

      {/* 模式切换 */}
      <Radio.Group
        value={mode}
        onChange={e => setMode(e.target.value)}
        style={{ marginBottom: 10 }}
        size="small"
        optionType="button"
        buttonStyle="solid"
        options={[
          { label: '可视化配置', value: 'visual' },
          { label: '手动输入', value: 'manual' },
        ]}
      />

      {mode === 'manual' ? (
        <Input
          value={manualCron}
          onChange={e => { setManualCron(e.target.value); onChange?.(e.target.value) }}
          placeholder="分 时 日 月 周  例: 0 1 * * *"
          style={{ fontFamily: 'monospace' }}
        />
      ) : (
        <Tabs
          size="small"
          items={[
            {
              key: 'minute', label: '分钟',
              children: <FieldPanel label="分钟" field={minute} min={0} max={59}
                onChange={f => handleFieldChange(setMinute, f, 'minute')} />
            },
            {
              key: 'hour', label: '小时',
              children: <FieldPanel label="小时" field={hour} min={0} max={23}
                onChange={f => handleFieldChange(setHour, f, 'hour')} />
            },
            {
              key: 'day', label: '日',
              children: <FieldPanel label="日" field={day} min={1} max={31}
                onChange={f => handleFieldChange(setDay, f, 'day')} />
            },
            {
              key: 'month', label: '月',
              children: <FieldPanel label="月" field={month} min={1} max={12} labels={MONTH_LABELS}
                onChange={f => handleFieldChange(setMonth, f, 'month')} />
            },
            {
              key: 'week', label: '星期',
              children: <FieldPanel label="星期" field={week} min={0} max={6} labels={WEEK_LABELS}
                onChange={f => handleFieldChange(setWeek, f, 'week')} />
            },
          ]}
        />
      )}

      {/* 当前表达式 + 最近执行时间（对齐 DS 调度预览） */}
      <div style={{ marginTop: 8, padding: '8px 12px', background: '#f5f5f5', borderRadius: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ color: '#999', fontSize: 12 }}>Cron 表达式：</span>
          <code style={{ fontSize: 14, color: '#1677ff', fontWeight: 600 }}>{currentCron || '未配置'}</code>
        </div>
        {quartzCron ? (
          <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
            发布到 DolphinScheduler：<code style={{ color: '#555' }}>{quartzCron}</code>
            <span style={{ marginLeft: 6, color: '#aaa' }}>（Quartz · Asia/Shanghai）</span>
          </div>
        ) : null}
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>最近 5 次执行时间</div>
          {previewError ? (
            <div style={{ fontSize: 12, color: '#cf1322' }}>{previewError}</div>
          ) : nextTimes.length ? (
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: '#333', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' }}>
              {nextTimes.map(t => (
                <li key={t} style={{ marginBottom: 2 }}>{t}</li>
              ))}
            </ol>
          ) : (
            <div style={{ fontSize: 12, color: '#bbb' }}>配置有效表达式后自动预览</div>
          )}
        </div>
      </div>
    </div>
  )
}
