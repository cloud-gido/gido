/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Form, Input, InputNumber, Select, Typography } from 'antd'

const { Paragraph } = Typography

export type OperatorResourceForm = {
  jm_memory: string
  jm_cpu: string
  tm_memory: string
  tm_cpu: string
  task_slots: string
  tm_replicas: string
}

export const EMPTY_OPERATOR_RESOURCES: OperatorResourceForm = {
  jm_memory: '',
  jm_cpu: '',
  tm_memory: '',
  tm_cpu: '',
  task_slots: '',
  tm_replicas: '',
}

function asObject(value: unknown): Record<string, any> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, any>
  if (value == null || String(value).trim() === '') return {}
  const parsed = JSON.parse(String(value))
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('invalid')
  return parsed
}

export function parseStreamRuntimeConfig(raw: unknown): {
  advancedJson: string
  resourceTier: string
  operatorResources: OperatorResourceForm
} {
  try {
    const obj = asObject(raw)
    const resources = obj.operator_resources || {}
    const advanced = { ...obj }
    delete advanced.resource_tier
    delete advanced.operator_resources
    return {
      advancedJson: JSON.stringify(advanced, null, 2),
      resourceTier: obj.resource_tier != null ? String(obj.resource_tier) : '',
      operatorResources: {
        jm_memory: resources.jobManager?.memory != null ? String(resources.jobManager.memory) : '',
        jm_cpu: resources.jobManager?.cpu != null ? String(resources.jobManager.cpu) : '',
        tm_memory: resources.taskManager?.memory != null ? String(resources.taskManager.memory) : '',
        tm_cpu: resources.taskManager?.cpu != null ? String(resources.taskManager.cpu) : '',
        task_slots: resources.taskSlots != null
          ? String(resources.taskSlots)
          : (resources.numberOfTaskSlots != null ? String(resources.numberOfTaskSlots) : ''),
        tm_replicas: resources.taskManager?.replicas != null ? String(resources.taskManager.replicas) : '',
      },
    }
  } catch {
    return {
      advancedJson: typeof raw === 'string' && raw.trim() ? raw : '{}',
      resourceTier: '',
      operatorResources: { ...EMPTY_OPERATOR_RESOURCES },
    }
  }
}

export function buildStreamRuntimeProperties(
  advancedJson: string,
  operatorResources: OperatorResourceForm,
  resourceTier?: string,
): string {
  const base = asObject(advancedJson.trim() || '{}')
  const tier = (resourceTier || '').trim()
  if (tier) base.resource_tier = tier
  else delete base.resource_tier

  const operator: Record<string, unknown> = {}
  const jobManager: Record<string, unknown> = {}
  const taskManager: Record<string, unknown> = {}
  if (operatorResources.jm_memory.trim()) jobManager.memory = operatorResources.jm_memory.trim()
  if (operatorResources.jm_cpu.trim()) jobManager.cpu = Number(operatorResources.jm_cpu)
  if (operatorResources.tm_memory.trim()) taskManager.memory = operatorResources.tm_memory.trim()
  if (operatorResources.tm_cpu.trim()) taskManager.cpu = Number(operatorResources.tm_cpu)
  if (operatorResources.tm_replicas.trim()) taskManager.replicas = Number(operatorResources.tm_replicas)
  if (Object.keys(jobManager).length) operator.jobManager = jobManager
  if (Object.keys(taskManager).length) operator.taskManager = taskManager
  if (operatorResources.task_slots.trim()) operator.taskSlots = Number(operatorResources.task_slots)
  if (Object.keys(operator).length) base.operator_resources = operator
  else delete base.operator_resources
  return Object.keys(base).length ? JSON.stringify(base) : ''
}

type Props = {
  resourceTier: string
  onResourceTierChange: (value: string) => void
  operatorResources: OperatorResourceForm
  onOperatorResourcesChange: (value: OperatorResourceForm) => void
  advancedJson: string
  onAdvancedJsonChange: (value: string) => void
  disabled?: boolean
  showAdvanced?: boolean
}

export default function StreamRuntimeConfig({
  resourceTier,
  onResourceTierChange,
  operatorResources,
  onOperatorResourcesChange,
  advancedJson,
  onAdvancedJsonChange,
  disabled,
  showAdvanced = true,
}: Props) {
  const patch = (value: Partial<OperatorResourceForm>) =>
    onOperatorResourcesChange({ ...operatorResources, ...value })

  return (
    <>
      <Form.Item label="规格模板">
        <Select
          allowClear
          placeholder="平台默认（不套用模板）"
          value={resourceTier || undefined}
          disabled={disabled}
          onChange={value => onResourceTierChange(value || '')}
          options={[
            { value: 'small', label: '小 — 轻量 SQL / 探查' },
            { value: 'medium', label: '中 — 默认生产' },
            { value: 'large', label: '大 — 高并行 / 重 SQL' },
          ]}
        />
      </Form.Item>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
        <Form.Item label="JM 内存" style={{ marginBottom: 0 }}>
          <Input placeholder="2048m" value={operatorResources.jm_memory} disabled={disabled}
            onChange={e => patch({ jm_memory: e.target.value })} />
        </Form.Item>
        <Form.Item label="JM CPU" style={{ marginBottom: 0 }}>
          <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
            value={operatorResources.jm_cpu ? Number(operatorResources.jm_cpu) : undefined}
            disabled={disabled}
            onChange={value => patch({ jm_cpu: value != null ? String(value) : '' })} />
        </Form.Item>
        <Form.Item label="TM 内存" style={{ marginBottom: 0 }}>
          <Input placeholder="4096m" value={operatorResources.tm_memory} disabled={disabled}
            onChange={e => patch({ tm_memory: e.target.value })} />
        </Form.Item>
        <Form.Item label="TM CPU" style={{ marginBottom: 0 }}>
          <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
            value={operatorResources.tm_cpu ? Number(operatorResources.tm_cpu) : undefined}
            disabled={disabled}
            onChange={value => patch({ tm_cpu: value != null ? String(value) : '' })} />
        </Form.Item>
        <Form.Item label="Task Slots" style={{ marginBottom: 0 }}>
          <InputNumber min={1} style={{ width: '100%' }} placeholder="2"
            value={operatorResources.task_slots ? Number(operatorResources.task_slots) : undefined}
            disabled={disabled}
            onChange={value => patch({ task_slots: value != null ? String(value) : '' })} />
        </Form.Item>
        <Form.Item label="TM 副本数" style={{ marginBottom: 0 }}>
          <InputNumber min={1} style={{ width: '100%' }} placeholder="自动"
            value={operatorResources.tm_replicas ? Number(operatorResources.tm_replicas) : undefined}
            disabled={disabled}
            onChange={value => patch({ tm_replicas: value != null ? String(value) : '' })} />
        </Form.Item>
      </div>
      {showAdvanced && <Form.Item label="高级 Flink 配置" style={{ marginTop: 16 }}>
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          JSON 顶级键会合并进 FlinkDeployment flinkConfiguration；Operator 资源请用上方表单。
        </Paragraph>
        <Input.TextArea
          rows={8}
          value={advancedJson}
          onChange={e => onAdvancedJsonChange(e.target.value)}
          disabled={disabled}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          placeholder={'{\n  "execution.checkpointing.interval": "60000"\n}'}
        />
      </Form.Item>}
    </>
  )
}
