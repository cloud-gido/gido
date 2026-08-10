/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Badge, Button, Card, Checkbox, Col, Descriptions, Divider, Empty, Form, Input,
  InputNumber, List, message, Modal, Radio, Row, Select, Space, Spin, Steps, Switch, Table, Tag,
  Typography,
} from 'antd'
import {
  ApartmentOutlined, ArrowLeftOutlined, ArrowRightOutlined, CheckCircleOutlined,
  DatabaseOutlined, PlusOutlined, SafetyCertificateOutlined, SaveOutlined,
} from '@ant-design/icons'
import {
  approvalApi, streamingApi,
  type StreamPipelineDefinition, type StreamPipelineExplain, type StreamPipelineMode,
  type StreamPipelineSpec,
} from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import StreamRuntimeConfig, {
  buildStreamRuntimeProperties,
  EMPTY_OPERATOR_RESOURCES,
  parseStreamRuntimeConfig,
  type OperatorResourceForm,
} from '../components/StreamRuntimeConfig'
import {
  buildLocalPipelineExplain, pipelineModeMeta, sanitizePipelineArtifact,
} from '../utils/streamPipeline'

const { Paragraph, Text, Title } = Typography
const PIPELINE_NAME_PATTERN = /^[a-z][a-z0-9-]{1,48}[a-z0-9]$/

const STEP_ITEMS = [
  { title: 'Source', description: '源端与语义' },
  { title: 'Schema', description: '字段与演进' },
  { title: 'Mapping / Transform', description: '映射与转换' },
  { title: 'Paimon Sink', description: '主键与分区' },
  { title: 'Runtime / Placement', description: '容量与调度' },
  { title: 'Preflight / Explain', description: '产物与风险' },
]

function emptyPipeline(workspaceId: number): StreamPipelineDefinition {
  return {
    workspace_id: workspaceId,
    name: '',
    description: '',
    mode: 'append',
    source: {
      connector: 'kafka', table: '', consumer_group: 'gido-pipeline',
      format: 'json', startup_mode: 'group-offsets',
    },
    schema: {
      evolution: 'strict',
      columns: [{ name: 'id', type: 'BIGINT', nullable: false, primary_key: true }],
    },
    mapping: { fields: [{ source: 'id', target: 'id' }] },
    sink: {
      catalog: 'paimon',
      database: 'ods',
      table: '',
      primary_keys: ['id'],
      partitions: [],
      bucket: 4,
    },
    runtime: { parallelism: 1 },
    placement: {
      requested_mode: 'recommend-only',
      sla_tier: 'standard',
      expected_records_per_second: 0,
      state_size_gb: 0,
      security_domain: 'default',
    },
  }
}

function normalizeItems(value: any): any[] {
  if (Array.isArray(value)) return value
  return Array.isArray(value?.items) ? value.items : (Array.isArray(value?.pipelines) ? value.pipelines : [])
}

function jobToPipeline(job: any): StreamPipelineDefinition {
  const spec = job.pipeline_spec || {}
  const projections = spec.transform?.projections || {}
  const sourceSchema = spec.source_schema || spec.schema || []
  const sourceFromExpression = (expression: unknown) => {
    const match = String(expression || '').match(/^`([^`]+)`$/)
    return match?.[1] || ''
  }
  return sanitizePipelineArtifact({
    id: job.id,
    workspace_id: job.workspace_id,
    name: job.name,
    description: spec.description || '',
    status: job.status || 'draft',
    mode: spec.mode || 'append',
    source: {
      connector: 'kafka',
      connection_profile_id: spec.source?.connection_profile_id,
      table: spec.source?.topic || '',
      consumer_group: spec.source?.consumer_group || 'gido-pipeline',
      format: spec.source?.format || 'json',
      startup_mode: spec.source?.startup_mode || 'group-offsets',
    },
    schema: {
      evolution: spec.schema_evolution || 'strict',
      contract_id: spec.schema_contract_id,
      version: spec.schema_version,
      source_columns: sourceSchema.map((field: any) => ({
        name: field.name,
        type: field.data_type,
        nullable: field.nullable,
      })),
      columns: (spec.schema || []).map((field: any) => ({
        name: field.name,
        type: field.data_type,
        nullable: field.nullable,
        primary_key: (spec.sink?.primary_keys || []).includes(field.name),
      })),
    },
    mapping: {
      fields: Object.keys(projections).length
        ? Object.entries(projections).map(([target, expression]) => ({
            source: sourceFromExpression(expression),
            target,
            expression: String(expression),
          }))
        : (spec.schema || []).map((field: any) => ({ source: field.name, target: field.name })),
      filter: spec.transform?.filter,
    },
    sink: {
      catalog: 'paimon',
      connection_profile_id: spec.sink?.connection_profile_id,
      database: spec.sink?.database || 'ods',
      table: spec.sink?.table || '',
      primary_keys: spec.sink?.primary_keys || [],
      partitions: spec.sink?.partition_keys || [],
      bucket: Number(spec.sink?.options?.bucket) || undefined,
    },
    runtime: {
      parallelism: job.parallelism || 1,
      streaming_properties: job.streaming_properties,
    },
    placement: {
      requested_mode: 'recommend-only',
      sla_tier: 'standard',
      expected_records_per_second: 0,
      state_size_gb: 0,
      security_domain: 'default',
    },
    created_at: job.created_at,
    updated_at: job.updated_at,
  }) as StreamPipelineDefinition
}

function toPipelineSpec(pipeline: StreamPipelineDefinition): StreamPipelineSpec {
  const projections = Object.fromEntries(pipeline.mapping.fields
    .filter(field => field.target.trim())
    .map(field => [field.target.trim(), field.expression?.trim() || `\`${field.source || field.target}\``]))
  const targetColumns = new Map(pipeline.schema.columns.map(column => [column.name.trim(), column]))
  const sourceColumns = new Map(
    (pipeline.schema.source_columns || []).map(column => [column.name.trim(), column]),
  )
  const activeSourceNames = new Set<string>()
  for (const field of pipeline.mapping.fields) {
    const sourceName = field.source.trim()
    const target = targetColumns.get(field.target.trim())
    if (sourceName && target) {
      activeSourceNames.add(sourceName)
      const existing = sourceColumns.get(sourceName)
      sourceColumns.set(sourceName, {
        name: sourceName,
        type: existing?.type || target.type,
        nullable: existing?.nullable ?? target.nullable,
      })
    }
  }
  return {
    spec_version: '1.0',
    kind: 'kafka_to_paimon',
    mode: pipeline.mode,
    schema_evolution: pipeline.schema.evolution,
    error_policy: 'fail-fast',
    schema_contract_id: pipeline.schema.contract_id,
    schema_version: pipeline.schema.version,
    source: {
      connector: 'kafka',
      connection_profile_id: Number(pipeline.source.connection_profile_id),
      topic: pipeline.source.table.trim(),
      consumer_group: pipeline.source.consumer_group?.trim() || 'gido-pipeline',
      format: (pipeline.source.format || 'json') as StreamPipelineSpec['source']['format'],
      startup_mode: (pipeline.source.startup_mode || 'group-offsets') as StreamPipelineSpec['source']['startup_mode'],
      options: {},
    },
    source_schema: [...sourceColumns.values()]
      .filter(column => activeSourceNames.has(column.name.trim()))
      .map(column => ({
      name: column.name.trim(),
      data_type: column.type.trim(),
      nullable: column.nullable !== false,
      })),
    sink: {
      connector: 'paimon',
      connection_profile_id: Number(pipeline.sink.connection_profile_id),
      database: pipeline.sink.database.trim(),
      table: pipeline.sink.table.trim(),
      primary_keys: pipeline.sink.primary_keys,
      partition_keys: pipeline.sink.partitions,
      options: pipeline.sink.bucket ? { bucket: String(pipeline.sink.bucket) } : {},
    },
    schema: pipeline.schema.columns.map(column => ({
      name: column.name.trim(),
      data_type: column.type.trim(),
      nullable: column.nullable !== false,
    })),
    transform: {
      projections,
      filter: pipeline.mapping.filter?.trim() || undefined,
    },
    description: pipeline.description?.trim() || undefined,
  }
}

function statusTag(status?: string) {
  const meta: Record<string, { color: string; label: string }> = {
    draft: { color: 'default', label: '草稿' },
    validated: { color: 'cyan', label: '已预检' },
    pending_approval: { color: 'gold', label: '待审批' },
    approved: { color: 'blue', label: '已批准' },
    running: { color: 'green', label: '运行中' },
    failed: { color: 'red', label: '失败' },
  }
  const item = meta[status || 'draft'] || { color: 'default', label: status || '草稿' }
  return <Tag color={item.color}>{item.label}</Tag>
}

function ErrorBoundaryHint({ children }: { children: React.ReactNode }) {
  return <div style={{ minWidth: 0 }}>{children}</div>
}

export default function StreamPipelinePage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_STREAM_WRITE, currentWorkspace)
  const [pipelines, setPipelines] = useState<StreamPipelineDefinition[]>([])
  const [draft, setDraft] = useState<StreamPipelineDefinition>(() => emptyPipeline(wsId || 0))
  const [step, setStep] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [profiles, setProfiles] = useState<any[]>([])
  const [explain, setExplain] = useState<StreamPipelineExplain | null>(null)
  const [confirmedRisks, setConfirmedRisks] = useState<string[]>([])
  const [advancedJson, setAdvancedJson] = useState('{}')
  const [resourceTier, setResourceTier] = useState('')
  const [operatorResources, setOperatorResources] = useState<OperatorResourceForm>({ ...EMPTY_OPERATOR_RESOURCES })
  const [profileModalOpen, setProfileModalOpen] = useState(false)
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileForm] = Form.useForm()

  const load = useCallback(async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const [pipelineResult, datasourceResult]: any[] = await Promise.all([
        streamingApi.listJobs(wsId).catch(() => []),
        streamingApi.listPipelineProfiles(wsId).catch(() => []),
      ])
      setPipelines(normalizeItems(pipelineResult)
        .filter((job: any) => job.definition_kind === 'pipeline')
        .map(jobToPipeline))
      setProfiles(normalizeItems(datasourceResult))
    } finally {
      setLoading(false)
    }
  }, [wsId])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!wsId) return
    setDraft(current => current.workspace_id === wsId ? current : emptyPipeline(wsId))
  }, [wsId])

  const patch = <K extends keyof StreamPipelineDefinition>(key: K, value: StreamPipelineDefinition[K]) => {
    setDraft(current => ({ ...current, [key]: value }))
    setExplain(null)
    setConfirmedRisks([])
  }

  const selectPipeline = (pipeline: StreamPipelineDefinition) => {
    const safe = sanitizePipelineArtifact(pipeline)
    setDraft(safe)
    const runtimeConfig = parseStreamRuntimeConfig(safe.runtime?.streaming_properties)
    setAdvancedJson(runtimeConfig.advancedJson)
    setResourceTier(safe.runtime?.resource_tier || runtimeConfig.resourceTier)
    setOperatorResources(runtimeConfig.operatorResources)
    setStep(0)
    setExplain(null)
    setConfirmedRisks([])
  }

  const newPipeline = () => {
    if (!wsId) return
    setDraft(emptyPipeline(wsId))
    setAdvancedJson('{}')
    setResourceTier('')
    setOperatorResources({ ...EMPTY_OPERATOR_RESOURCES })
    setStep(0)
    setExplain(null)
    setConfirmedRisks([])
  }

  const createProfile = async () => {
    if (!wsId) return
    const values = await profileForm.validateFields()
    let secretRefs: Record<string, string> = {}
    try {
      secretRefs = JSON.parse(values.secret_refs_json?.trim() || '{}')
      if (!secretRefs || Array.isArray(secretRefs) || typeof secretRefs !== 'object') throw new Error()
    } catch {
      message.error('Secret 引用必须是 JSON 对象')
      return
    }
    const options = values.connector_type === 'kafka'
      ? {
          'bootstrap.servers': values.bootstrap_servers,
          ...(values.security_protocol ? { 'security.protocol': values.security_protocol } : {}),
          ...(values.sasl_mechanism ? { 'sasl.mechanism': values.sasl_mechanism } : {}),
          ...(values.schema_registry_url ? { 'schema.registry.url': values.schema_registry_url } : {}),
        }
      : {
          warehouse: values.warehouse,
          'allowed.namespaces': values.allowed_namespaces,
          ...(values.metastore ? { metastore: values.metastore } : {}),
          ...(values.uri ? { uri: values.uri } : {}),
        }
    setProfileSaving(true)
    try {
      await streamingApi.createPipelineProfile({
        workspace_id: wsId,
        name: values.name,
        connector_type: values.connector_type,
        options,
        secret_refs: secretRefs,
      })
      message.success('连接配置已创建')
      setProfileModalOpen(false)
      profileForm.resetFields()
      await load()
    } finally {
      setProfileSaving(false)
    }
  }

  const validateStep = (targetStep = step): boolean => {
    if (targetStep === 0 && (!draft.name.trim() || !draft.source.connection_profile_id || !draft.source.table.trim() || !draft.source.consumer_group?.trim())) {
      message.warning('请填写管道名称、Kafka 连接配置、Topic 和 Consumer Group')
      return false
    }
    if (targetStep === 0 && !PIPELINE_NAME_PATTERN.test(draft.name)) {
      message.warning('管道名称须为 3-50 位小写字母、数字或短横线，且以字母开头')
      return false
    }
    if (targetStep === 1 && (!draft.schema.columns.length || draft.schema.columns.some(column => !column.name.trim() || !column.type.trim()))) {
      message.warning('Schema 至少需要一个完整字段')
      return false
    }
    if (targetStep === 2 && draft.mapping.fields.some(field => !field.source.trim() || !field.target.trim())) {
      message.warning('每个映射都需要源字段和目标字段')
      return false
    }
    if (targetStep === 3) {
      if (!draft.sink.connection_profile_id || !draft.sink.database.trim() || !draft.sink.table.trim()) {
        message.warning('请选择 Paimon 连接配置并填写数据库和表名')
        return false
      }
      if (draft.mode !== 'append' && draft.sink.primary_keys.length === 0) {
        message.warning('Upsert / CDC 必须配置主键')
        return false
      }
      if (draft.sink.primary_keys.length > 0
        && draft.sink.partitions.some(key => !draft.sink.primary_keys.includes(key))) {
        message.warning('Paimon 主键表要求所有分区字段同时属于主键')
        return false
      }
      if (draft.schema.columns.some(column => draft.sink.primary_keys.includes(column.name) && column.nullable !== false)) {
        message.warning('Paimon 主键字段必须设置为不可空')
        return false
      }
    }
    return true
  }

  const materializeDraft = (): StreamPipelineDefinition | null => {
    try {
      const streamingProperties = buildStreamRuntimeProperties(advancedJson, operatorResources, resourceTier)
      return sanitizePipelineArtifact({
        ...draft,
        runtime: {
          ...draft.runtime,
          resource_tier: resourceTier || undefined,
          streaming_properties: streamingProperties,
        },
      })
    } catch {
      message.error('高级 Flink 配置 JSON 格式无效')
      return null
    }
  }

  const runPreflight = async () => {
    for (let index = 0; index < 5; index += 1) {
      if (!validateStep(index)) {
        setStep(index)
        return
      }
    }
    const payload = materializeDraft()
    if (!payload) return
    const spec = toPipelineSpec(payload)
    setPreflightLoading(true)
    try {
      const [check, artifact, placement]: any[] = await Promise.all([
        streamingApi.preflightPipeline(spec),
        streamingApi.compilePipeline(spec),
        streamingApi.previewPipelinePlacement({
          workspace_id: payload.workspace_id,
          job_id: payload.id,
          requested_mode: payload.placement.requested_mode || 'recommend-only',
          sla_tier: payload.placement.sla_tier || 'standard',
          stateful: payload.mode !== 'append',
          state_size_gb: payload.placement.state_size_gb || 0,
          security_domain: payload.placement.security_domain || 'default',
          parallelism: payload.runtime.parallelism,
          expected_records_per_second: payload.placement.expected_records_per_second || 0,
        }),
      ])
      const local = buildLocalPipelineExplain(payload as unknown as Record<string, any>)
      const failedChecks = (check?.checks || []).filter((item: any) => item.status !== 'passed')
      const stateRisks = local.risks.filter((risk: any) => risk.code !== 'PRIMARY_KEY_REQUIRED')
      setExplain(sanitizePipelineArtifact({
        generated_artifact: {
          kind: 'flink_sql',
          content: artifact?.sql || '',
          runner: artifact?.runner || {},
          redacted: true,
        },
        schema_diff: check?.schema_diff?.changes?.map((item: any) => ({
          column: item.field,
          change: item.change,
          detail: item.severity,
        })) || local.schema_diff,
        placement: {
          decision: placement?.target_group_id
            ? `${placement.mode} → DeploymentGroup ${placement.target_group_id}`
            : (placement?.mode || local.placement.decision),
          resource_tier: resourceTier || local.placement.resource_tier,
          parallelism: payload.runtime.parallelism,
          capacity: placement?.resources
            ? `${placement.resources.task_slots} slots / ${placement.resources.taskmanager_replicas} TM / ${placement.resources.memory_mb} MB`
            : undefined,
        },
        risks: [
          ...failedChecks.map((item: any) => ({
            code: String(item.check || 'PREFLIGHT_FAILED').toUpperCase(),
            level: 'blocker',
            message: typeof item.detail === 'string' ? item.detail : `${item.check} 未通过`,
            requires_confirmation: false,
          })),
          ...stateRisks,
        ],
        valid: Boolean(check?.ok) && failedChecks.length === 0,
      }))
    } catch (error: any) {
      const local = buildLocalPipelineExplain(payload as unknown as Record<string, any>) as StreamPipelineExplain
      setExplain(local)
      const safeError = sanitizePipelineArtifact(error?.response?.data?.detail || error?.message || '')
      message.info(`后端预检不可用，当前展示本地安全预览${safeError ? `：${String(safeError)}` : ''}`)
    } finally {
      setPreflightLoading(false)
    }
  }

  useEffect(() => {
    if (step === 5 && !explain && !preflightLoading) void runPreflight()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step])

  const persist = async (submitApproval: boolean) => {
    if (!canWrite) return
    const payload = materializeDraft()
    if (!payload) return
    const requiredRisks = explain?.risks.filter(risk => risk.requires_confirmation).map(risk => risk.code) || []
    if (submitApproval && (!explain?.valid || requiredRisks.some(code => !confirmedRisks.includes(code)))) {
      message.warning('请先通过预检并确认全部风险')
      return
    }
    setSaving(true)
    try {
      let spec = toPipelineSpec(payload)
      if (submitApproval) {
        let contractId = payload.schema.contract_id
        if (!contractId) {
          const contract: any = await streamingApi.createPipelineSchemaContract({
            workspace_id: payload.workspace_id,
            name: `${payload.name}-schema`,
            compatibility_mode: 'backward',
            description: `数据管道 ${payload.name} 的生产 Schema Contract`,
          })
          contractId = Number(contract.id)
        }
        const versionResult: any = await streamingApi.createPipelineSchemaVersion(
          Number(contractId),
          {
            fields: spec.schema,
            change_note: `数据管道 ${payload.name} 发布`,
            allow_breaking: false,
          },
        )
        const schemaVersion = Number(versionResult?.version?.version)
        spec = {
          ...spec,
          schema_contract_id: Number(contractId),
          schema_version: schemaVersion,
        }
        payload.schema = {
          ...payload.schema,
          contract_id: Number(contractId),
          version: schemaVersion,
        }
        setDraft(current => ({
          ...current,
          schema: {
            ...current.schema,
            contract_id: Number(contractId),
            version: schemaVersion,
          },
        }))
      }
      const runtimePayload = {
        name: payload.name,
        parallelism: payload.runtime.parallelism,
        streaming_properties: payload.runtime.streaming_properties,
        definition_kind: 'pipeline',
        pipeline_spec: spec,
      }
      const saved: any = payload.id
        ? await streamingApi.updateJob(payload.id, runtimePayload)
        : await streamingApi.createJob({
          workspace_id: payload.workspace_id,
          job_type: 'SQL',
          flink_sql_submit_mode: 'flink_operator',
          ...runtimePayload,
        })
      const savedPipeline = jobToPipeline(saved)
      if (submitApproval) {
        const release: any = await streamingApi.createRelease(Number(savedPipeline.id), {
          release_note: `数据管道发布；风险确认：${confirmedRisks.join(', ') || '无'}`,
        })
        await approvalApi.submit({
          workspace_id: payload.workspace_id,
          resource_type: 'stream_job',
          resource_id: Number(savedPipeline.id),
          release_id: release?.id,
          action: 'submit_job',
          submit_note: '数据管道发布',
        })
        message.success('已创建发布版本并提交审批')
      } else {
        message.success('管道草稿已保存')
      }
      setDraft(savedPipeline)
      await load()
    } catch (error: any) {
      const safeError = sanitizePipelineArtifact(
        error?.response?.data?.detail || error?.message || '保存失败；请确认管道 API 已实现',
      )
      message.error(String(safeError))
    } finally {
      setSaving(false)
    }
  }

  const mode = pipelineModeMeta(draft.mode)
  const requiredRisks = explain?.risks.filter(risk => risk.requires_confirmation) || []
  const releaseReady = Boolean(explain?.valid)
    && requiredRisks.every(risk => confirmedRisks.includes(risk.code))

  const schemaColumns = useMemo(() => draft.schema.columns.map(column => ({
    ...column,
    key: column.name,
  })), [draft.schema.columns])

  const renderStep = () => {
    if (step === 0) {
      return (
        <ErrorBoundaryHint>
          <Alert showIcon type="info" style={{ marginBottom: 16 }}
            message="只引用凭据，不采集密钥"
            description="连接配置只暴露 Secret 引用键名。控制台、预检结果和生成产物不会显示密码、Token 或 Access Key。" />
          <Form layout="vertical">
            <Row gutter={16}>
              <Col xs={24} lg={12}>
                <Form.Item label="管道名称" required>
                  <Input value={draft.name} maxLength={50} placeholder="kafka-orders-to-paimon"
                    onChange={event => patch('name', event.target.value)} />
                </Form.Item>
              </Col>
              <Col xs={24} lg={12}>
                <Form.Item label="写入语义" required>
                  <Radio.Group value={draft.mode} buttonStyle="solid"
                    onChange={event => {
                      const nextMode = event.target.value as StreamPipelineMode
                      patch('mode', nextMode)
                      if (nextMode === 'cdc' && draft.source.format === 'json') {
                        patch('source', { ...draft.source, format: 'debezium-json' })
                      }
                    }}>
                    <Radio.Button value="append">Append</Radio.Button>
                    <Radio.Button value="upsert">Upsert</Radio.Button>
                    <Radio.Button value="cdc">CDC</Radio.Button>
                  </Radio.Group>
                </Form.Item>
              </Col>
            </Row>
            <Alert type={draft.mode === 'cdc' ? 'warning' : 'success'} showIcon style={{ marginBottom: 16 }}
              message={<Space><Tag color={mode.color}>{mode.label}</Tag>{mode.description}</Space>} />
            <Row gutter={16}>
              <Col xs={24} lg={12}>
                <Form.Item label="Kafka 连接配置" required>
                  <Select allowClear showSearch optionFilterProp="label" value={draft.source.connection_profile_id}
                    onChange={value => patch('source', { ...draft.source, connection_profile_id: value })}
                    placeholder="选择 Kafka Profile"
                    options={profiles.filter(profile => profile.connector_type === 'kafka').map(profile => ({
                      value: profile.id,
                      label: `${profile.name} · Secret refs: ${(profile.secret_ref_keys || []).join(', ') || '无'}`,
                    }))} />
                </Form.Item>
              </Col>
              <Col xs={24} lg={12}>
                <Form.Item label="消息格式" required>
                  <Select value={draft.source.format}
                    onChange={value => patch('source', { ...draft.source, format: value })}
                    options={[
                      { value: 'json', label: 'JSON（Append / Upsert）' },
                      { value: 'debezium-json', label: 'Debezium JSON（CDC）' },
                      { value: 'canal-json', label: 'Canal JSON（CDC）' },
                      { value: 'maxwell-json', label: 'Maxwell JSON（CDC）' },
                    ]} />
                </Form.Item>
              </Col>
              <Col xs={24} lg={12}>
                <Form.Item label="Kafka Topic" required>
                  <Input value={draft.source.table} placeholder="shop.orders.changelog"
                    onChange={event => patch('source', { ...draft.source, table: event.target.value })} />
                </Form.Item>
              </Col>
              <Col xs={24} lg={12}>
                <Form.Item label="Consumer Group" required>
                  <Input value={draft.source.consumer_group} placeholder="gido-orders-pipeline"
                    onChange={event => patch('source', { ...draft.source, consumer_group: event.target.value })} />
                </Form.Item>
              </Col>
              <Col xs={24} lg={12}>
                <Form.Item label="启动位点">
                  <Select value={draft.source.startup_mode}
                    onChange={value => patch('source', { ...draft.source, startup_mode: value })}
                    options={[
                      { value: 'group-offsets', label: 'Consumer Group offsets' },
                      { value: 'earliest-offset', label: 'Earliest' },
                      { value: 'latest-offset', label: 'Latest' },
                    ]} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item label="说明">
              <Input.TextArea rows={3} value={draft.description} maxLength={500}
                onChange={event => patch('description', event.target.value)} />
            </Form.Item>
          </Form>
        </ErrorBoundaryHint>
      )
    }

    if (step === 1) {
      return (
        <>
          <Alert showIcon type="info" style={{ marginBottom: 16 }} message="Schema 合约"
            description={draft.schema.contract_id
              ? `已绑定 Contract #${draft.schema.contract_id} v${draft.schema.version || 'latest'}；新版本发布前必须通过兼容性检查。`
              : '首次发布会创建不可变 Schema Contract；后续发布仅允许兼容演进，breaking 变更由管理员单独审批。'} />
          <Form layout="vertical">
            <Form.Item label="演进策略">
              <Select value={draft.schema.evolution}
                onChange={value => patch('schema', { ...draft.schema, evolution: value })}
                options={[
                  { value: 'strict', label: 'Strict — 任何差异都阻断' },
                  { value: 'additive', label: 'Additive — 允许兼容新增与类型拓宽' },
                ]} />
            </Form.Item>
          </Form>
          <Table size="small" pagination={false} rowKey={(_, index) => String(index)}
            dataSource={draft.schema.columns}
            columns={[
              {
                title: '字段', render: (_: unknown, row, index) => <Input value={row.name} placeholder="column_name"
                  onChange={event => {
                    const columns = [...draft.schema.columns]
                    columns[index] = { ...row, name: event.target.value }
                    patch('schema', { ...draft.schema, columns })
                  }} />,
              },
              {
                title: 'Flink 类型', width: 200, render: (_: unknown, row, index) => <Select value={row.type} style={{ width: '100%' }}
                  onChange={value => {
                    const columns = [...draft.schema.columns]
                    columns[index] = { ...row, type: value }
                    patch('schema', { ...draft.schema, columns })
                  }} options={['STRING', 'BIGINT', 'INT', 'DECIMAL(18,2)', 'BOOLEAN', 'DATE', 'TIMESTAMP(3)'].map(value => ({ value }))} />,
              },
              {
                title: '可空', width: 80, render: (_: unknown, row, index) => <Switch checked={row.nullable !== false}
                  onChange={checked => {
                    const columns = [...draft.schema.columns]
                    columns[index] = { ...row, nullable: checked }
                    patch('schema', { ...draft.schema, columns })
                  }} />,
              },
              {
                title: '主键', width: 80, render: (_: unknown, row, index) => <Checkbox checked={row.primary_key}
                  onChange={event => {
                    const columns = [...draft.schema.columns]
                    columns[index] = { ...row, primary_key: event.target.checked }
                    patch('schema', { ...draft.schema, columns })
                    patch('sink', { ...draft.sink, primary_keys: columns.filter(item => item.primary_key).map(item => item.name) })
                  }} />,
              },
              {
                title: '', width: 70, render: (_: unknown, _row, index) => <Button danger type="link"
                  onClick={() => patch('schema', { ...draft.schema, columns: draft.schema.columns.filter((_, i) => i !== index) })}>删除</Button>,
              },
            ]} />
          <Button style={{ marginTop: 12 }} icon={<PlusOutlined />}
            onClick={() => patch('schema', { ...draft.schema, columns: [...draft.schema.columns, { name: '', type: 'STRING', nullable: true }] })}>
            添加字段
          </Button>
        </>
      )
    }

    if (step === 2) {
      return (
        <>
          <Alert showIcon type="warning" style={{ marginBottom: 16 }} message="转换表达式将进入生成 SQL"
            description="仅填写字段表达式，不要粘贴连接串或密钥。预检产物仍会执行二次脱敏。" />
          <Table size="small" pagination={false} rowKey={(_, index) => String(index)}
            dataSource={draft.mapping.fields}
            columns={[
              {
                title: '源字段', width: 220, render: (_: unknown, row, index) => <Select showSearch allowClear
                  value={row.source || undefined} style={{ width: '100%' }}
                  onChange={value => {
                    const fields = [...draft.mapping.fields]
                    fields[index] = { ...row, source: value || '' }
                    patch('mapping', { ...draft.mapping, fields })
                  }} options={schemaColumns.map(column => ({ value: column.name, label: column.name }))} />,
              },
              {
                title: '目标字段', width: 220, render: (_: unknown, row, index) => <Input value={row.target}
                  onChange={event => {
                    const fields = [...draft.mapping.fields]
                    fields[index] = { ...row, target: event.target.value }
                    patch('mapping', { ...draft.mapping, fields })
                  }} />,
              },
              {
                title: '转换表达式（可选）', render: (_: unknown, row, index) => <Input value={row.expression}
                  placeholder="CAST(amount AS DECIMAL(18,2))"
                  onChange={event => {
                    const fields = [...draft.mapping.fields]
                    fields[index] = { ...row, expression: event.target.value }
                    patch('mapping', { ...draft.mapping, fields })
                  }} />,
              },
              {
                title: '', width: 70, render: (_: unknown, _row, index) => <Button danger type="link"
                  onClick={() => patch('mapping', { ...draft.mapping, fields: draft.mapping.fields.filter((_, i) => i !== index) })}>删除</Button>,
              },
            ]} />
          <Space style={{ marginTop: 12 }}>
            <Button icon={<PlusOutlined />} onClick={() => patch('mapping', {
              ...draft.mapping, fields: [...draft.mapping.fields, { source: '', target: '' }],
            })}>添加映射</Button>
            <Button onClick={() => patch('mapping', {
              ...draft.mapping,
              fields: draft.schema.columns.map(column => ({ source: column.name, target: column.name })),
            })}>按同名字段生成</Button>
          </Space>
          <Form layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item label="行过滤（可选）">
              <Input value={draft.mapping.filter} placeholder="is_deleted = FALSE"
                onChange={event => patch('mapping', { ...draft.mapping, filter: event.target.value })} />
            </Form.Item>
          </Form>
        </>
      )
    }

    if (step === 3) {
      return (
        <Form layout="vertical">
          <Alert showIcon type={draft.mode === 'append' ? 'info' : 'warning'} style={{ marginBottom: 16 }}
            message={`${mode.label} → Paimon`}
            description={draft.mode === 'append'
              ? 'Append 表可不设主键；重复事件不会自动去重。'
              : '主键决定状态分区与更新语义。变更主键通常需要有状态迁移确认。'} />
          <Row gutter={16}>
            <Col xs={24} md={8}><Form.Item label="Paimon 连接配置" required>
              <Select showSearch optionFilterProp="label" value={draft.sink.connection_profile_id}
                placeholder="选择 Paimon Profile"
                onChange={value => patch('sink', { ...draft.sink, connection_profile_id: value })}
                options={profiles.filter(profile => profile.connector_type === 'paimon').map(profile => ({
                  value: profile.id,
                  label: `${profile.name} · Secret refs: ${(profile.secret_ref_keys || []).join(', ') || '无'}`,
                }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="数据库" required><Input value={draft.sink.database}
              onChange={event => patch('sink', { ...draft.sink, database: event.target.value })} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="目标表" required><Input value={draft.sink.table}
              onChange={event => patch('sink', { ...draft.sink, table: event.target.value })} /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Bucket"><InputNumber min={1} max={256} style={{ width: '100%' }}
              value={draft.sink.bucket} onChange={value => patch('sink', { ...draft.sink, bucket: value || undefined })} /></Form.Item></Col>
          </Row>
          <Form.Item label="主键" required={draft.mode !== 'append'}>
            <Select mode="multiple" value={draft.sink.primary_keys}
              onChange={value => patch('sink', { ...draft.sink, primary_keys: value })}
              options={schemaColumns.map(column => ({ value: column.name }))} />
          </Form.Item>
          <Form.Item label="分区字段">
            <Select mode="multiple" value={draft.sink.partitions}
              onChange={value => patch('sink', { ...draft.sink, partitions: value })}
              options={schemaColumns.map(column => ({ value: column.name }))} />
          </Form.Item>
        </Form>
      )
    }

    if (step === 4) {
      return (
        <Form layout="vertical">
          <Alert showIcon type="info" style={{ marginBottom: 16 }} message="Flink Operator 运行时"
            description="资源配置复用 Stream 作业统一模板。预检会结合容量输出 Placement 决策；当前 Flink SQL 后端固定 fail-fast，原始字节 DLQ 将由受控 Runner 阶段提供。" />
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="并行度">
                <InputNumber min={1} max={1024} value={draft.runtime.parallelism} style={{ width: '100%' }}
                  onChange={value => patch('runtime', { ...draft.runtime, parallelism: value || 1 })} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="资源池">
                <Select allowClear value={draft.placement.pool} placeholder="工作空间默认"
                  onChange={value => patch('placement', { ...draft.placement, pool: value })}
                  options={[
                    { value: 'stream-general', label: 'stream-general' },
                    { value: 'stream-stateful', label: 'stream-stateful' },
                    { value: 'stream-high-throughput', label: 'stream-high-throughput' },
                  ]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Namespace（可选）">
                <Input value={draft.placement.namespace} placeholder="由平台策略决定"
                  onChange={event => patch('placement', { ...draft.placement, namespace: event.target.value })} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item label="隔离策略">
                <Select value={draft.placement.requested_mode || 'recommend-only'}
                  onChange={value => patch('placement', { ...draft.placement, requested_mode: value })}
                  options={[
                    { value: 'recommend-only', label: '平台建议（不自动迁移）' },
                    { value: 'dedicated', label: '独立 Deployment' },
                    { value: 'grouped', label: '允许兼容组共享' },
                  ]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="SLA 等级">
                <Select value={draft.placement.sla_tier || 'standard'}
                  onChange={value => patch('placement', { ...draft.placement, sla_tier: value })}
                  options={[
                    { value: 'best-effort', label: 'Best Effort' },
                    { value: 'standard', label: 'Standard' },
                    { value: 'high', label: 'High' },
                    { value: 'critical', label: 'Critical（强制隔离）' },
                  ]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="安全域">
                <Input value={draft.placement.security_domain || 'default'}
                  onChange={event => patch('placement', { ...draft.placement, security_domain: event.target.value })} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="预计吞吐（records/s）">
                <InputNumber min={0} style={{ width: '100%' }}
                  value={draft.placement.expected_records_per_second || 0}
                  onChange={value => patch('placement', {
                    ...draft.placement, expected_records_per_second: Number(value || 0),
                  })} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="预计状态量（GB）">
                <InputNumber min={0} precision={2} style={{ width: '100%' }}
                  value={draft.placement.state_size_gb || 0}
                  onChange={value => patch('placement', {
                    ...draft.placement, state_size_gb: Number(value || 0),
                  })} />
              </Form.Item>
            </Col>
          </Row>
          <Divider orientation="left">Operator 资源</Divider>
          <StreamRuntimeConfig resourceTier={resourceTier} onResourceTierChange={setResourceTier}
            operatorResources={operatorResources} onOperatorResourcesChange={setOperatorResources}
            advancedJson={advancedJson} onAdvancedJsonChange={setAdvancedJson} />
        </Form>
      )
    }

    return (
      <Spin spinning={preflightLoading}>
        {explain?.local_fallback && <Alert showIcon type="warning" style={{ marginBottom: 16 }}
          message="本地安全预览" description="后端 preflight 尚未实现；可检查定义，但在服务端预检可用前不能真实保存或发布。" />}
        {!explain ? <Empty description="正在生成预检结果" /> : (
          <>
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={14}>
                <Card size="small" title={<Space><SafetyCertificateOutlined />生成 SQL / Runner Artifact<Tag color="green">已脱敏</Tag></Space>}>
                  <pre style={{
                    margin: 0, padding: 14, borderRadius: 6, overflow: 'auto', maxHeight: 360,
                    background: '#0f172a', color: '#e2e8f0', fontSize: 12, lineHeight: 1.6,
                  }}>{explain.generated_artifact?.content || '—'}</pre>
                  {explain.generated_artifact?.runner && (
                    <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
                      <Descriptions.Item label="Runner">
                        <Text code>{JSON.stringify(explain.generated_artifact.runner)}</Text>
                      </Descriptions.Item>
                    </Descriptions>
                  )}
                  <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                    Artifact 仅用于审核展示；凭据在运行时由 Secret 引用解析。
                  </Paragraph>
                </Card>
              </Col>
              <Col xs={24} xl={10}>
                <Card size="small" title={<Space><ApartmentOutlined />容量与 Placement 决策</Space>} style={{ marginBottom: 16 }}>
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="决策">{explain.placement?.decision || '—'}</Descriptions.Item>
                    <Descriptions.Item label="规格">{explain.placement?.resource_tier || resourceTier || '平台默认'}</Descriptions.Item>
                    <Descriptions.Item label="并行度">{explain.placement?.parallelism || draft.runtime.parallelism}</Descriptions.Item>
                    <Descriptions.Item label="容量">{explain.placement?.capacity || '等待服务端容量评估'}</Descriptions.Item>
                  </Descriptions>
                </Card>
                <Card size="small" title="Schema Diff">
                  <List size="small" dataSource={explain.schema_diff || []}
                    locale={{ emptyText: '目标 Schema 无变化' }}
                    renderItem={item => <List.Item>
                      <Space><Tag color={item.change?.includes('DROP') ? 'red' : 'blue'}>{item.change}</Tag>
                        <Text code>{item.column}</Text><Text type="secondary">{item.type || item.detail}</Text></Space>
                    </List.Item>} />
                </Card>
              </Col>
            </Row>
            <Card size="small" title="风险确认" style={{ marginTop: 16 }}>
              {!explain.risks?.length ? <Alert showIcon type="success" message="未发现需要确认的风险" /> : (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {explain.risks.map(risk => (
                    <Alert key={risk.code} showIcon
                      type={risk.level === 'blocker' ? 'error' : risk.level === 'high' ? 'warning' : 'info'}
                      message={<Space>
                        <Tag color={risk.level === 'blocker' ? 'red' : risk.level === 'high' ? 'orange' : 'blue'}>{risk.level.toUpperCase()}</Tag>
                        {risk.message}
                      </Space>}
                      action={risk.requires_confirmation ? <Checkbox
                        checked={confirmedRisks.includes(risk.code)}
                        onChange={event => setConfirmedRisks(current => event.target.checked
                          ? [...new Set([...current, risk.code])]
                          : current.filter(code => code !== risk.code))}>我已理解并确认</Checkbox> : undefined} />
                  ))}
                </Space>
              )}
            </Card>
          </>
        )}
      </Spin>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>数据管道</Title>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            从数据源契约生成可审计的 Flink → Paimon 管道；预检先于发布，密钥始终由 Secret 引用。
          </Paragraph>
        </div>
        <Space>
          <Button onClick={() => setProfileModalOpen(true)} disabled={!canWrite}>连接配置</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={newPipeline} disabled={!canWrite}>新建管道</Button>
        </Space>
      </div>
      {!canWrite && (
        <Tag style={{ marginBottom: 16 }}>只读 · 可查看管道，保存与提交需写权限</Tag>
      )}
      <Row gutter={16} align="stretch">
        <Col xs={24} xxl={5}>
          <Card size="small" title={<Space><DatabaseOutlined />管道定义<Badge count={pipelines.length} showZero /></Space>}
            style={{ marginBottom: 16 }} bodyStyle={{ padding: 8 }}>
            <Spin spinning={loading}>
              <List dataSource={pipelines} locale={{ emptyText: '暂无已保存管道' }}
                renderItem={pipeline => (
                  <List.Item onClick={() => selectPipeline(pipeline)}
                    style={{
                      cursor: 'pointer', borderRadius: 6, padding: 10,
                      background: pipeline.id && pipeline.id === draft.id ? '#f0f5ff' : undefined,
                    }}>
                    <List.Item.Meta title={<Space size={4}><Text ellipsis style={{ maxWidth: 160 }}>{pipeline.name}</Text>{statusTag(pipeline.status)}</Space>}
                      description={<Space size={4}><Tag color={pipelineModeMeta(pipeline.mode).color}>{pipelineModeMeta(pipeline.mode).label}</Tag>
                        <Text type="secondary" ellipsis>{pipeline.sink?.database}.{pipeline.sink?.table}</Text></Space>} />
                  </List.Item>
                )} />
            </Spin>
          </Card>
        </Col>
        <Col xs={24} xxl={19}>
          <Card bodyStyle={{ paddingTop: 18 }}>
            <Steps current={step} items={STEP_ITEMS} responsive style={{ marginBottom: 24 }} />
            <Divider />
            <div style={{ minHeight: 430 }}>{renderStep()}</div>
            <Divider />
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <Button icon={<ArrowLeftOutlined />} disabled={step === 0} onClick={() => setStep(current => current - 1)}>上一步</Button>
              <Space wrap>
                <Button icon={<SaveOutlined />} disabled={!canWrite} loading={saving}
                  onClick={() => void persist(false)}>保存草稿</Button>
                {step === 5 ? (
                  <>
                    <Button onClick={() => void runPreflight()} loading={preflightLoading}>重新预检</Button>
                    <Button type="primary" icon={<CheckCircleOutlined />} loading={saving}
                      disabled={!canWrite || !releaseReady || explain?.local_fallback}
                      onClick={() => void persist(true)}>保存并提交审批</Button>
                  </>
                ) : (
                  <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => {
                    if (validateStep()) setStep(current => current + 1)
                  }}>下一步</Button>
                )}
              </Space>
            </div>
          </Card>
        </Col>
      </Row>
      <Modal title="新建连接配置" open={profileModalOpen}
        onCancel={() => setProfileModalOpen(false)} onOk={() => void createProfile()}
        confirmLoading={profileSaving} destroyOnClose>
        <Form form={profileForm} layout="vertical" initialValues={{ connector_type: 'kafka' }}>
          <Form.Item name="name" label="配置名称" rules={[{ required: true }]}>
            <Input placeholder="kafka-prod / paimon-warehouse" />
          </Form.Item>
          <Form.Item name="connector_type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'kafka', label: 'Kafka' },
              { value: 'paimon', label: 'Paimon' },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(before, after) => before.connector_type !== after.connector_type}>
            {({ getFieldValue }) => getFieldValue('connector_type') === 'kafka' ? (
              <>
                <Form.Item name="bootstrap_servers" label="Bootstrap Servers" rules={[{ required: true }]}>
                  <Input placeholder="kafka-1:9092,kafka-2:9092" />
                </Form.Item>
                <Row gutter={12}>
                  <Col span={12}><Form.Item name="security_protocol" label="Security Protocol">
                    <Select allowClear options={['PLAINTEXT', 'SSL', 'SASL_PLAINTEXT', 'SASL_SSL'].map(value => ({ value }))} />
                  </Form.Item></Col>
                  <Col span={12}><Form.Item name="sasl_mechanism" label="SASL Mechanism">
                    <Select allowClear options={['PLAIN', 'SCRAM-SHA-256', 'SCRAM-SHA-512'].map(value => ({ value }))} />
                  </Form.Item></Col>
                </Row>
                <Form.Item name="schema_registry_url" label="Schema Registry（可选）">
                  <Input placeholder="https://schema-registry.internal" />
                </Form.Item>
              </>
            ) : (
              <>
                <Form.Item name="warehouse" label="Warehouse" rules={[{ required: true }]}>
                  <Input placeholder="s3://warehouse/paimon" />
                </Form.Item>
                <Form.Item name="allowed_namespaces" label="允许写入的数据库" rules={[{ required: true }]}
                  extra="逗号分隔；编译预检会阻断白名单外的目标表。">
                  <Input placeholder="ods,dwd" />
                </Form.Item>
                <Row gutter={12}>
                  <Col span={12}><Form.Item name="metastore" label="Metastore"><Input /></Form.Item></Col>
                  <Col span={12}><Form.Item name="uri" label="Catalog URI"><Input /></Form.Item></Col>
                </Row>
              </>
            )}
          </Form.Item>
          <Form.Item name="secret_refs_json" label="Secret 引用（JSON）"
            extra={'键是 Connector option，值是 scope=stream/all 且标记为 Secret 的工作空间变量名。'}>
            <Input.TextArea rows={4}
              placeholder={'{\n  "properties.sasl.jaas.config": "kafka_sasl_jaas"\n}'} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
