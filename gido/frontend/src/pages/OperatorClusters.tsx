/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Typography, message,
} from 'antd'
import { ClusterOutlined, DeleteOutlined, EditOutlined, MinusCircleOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { R } from '../routes'

type RuntimeImageEntry = {
  label?: string | null
  image: string
  flink_version?: string | null
  is_default?: boolean
}

type OperatorProfile = {
  id: number
  name: string
  description?: string | null
  is_default: boolean
  is_enabled: boolean
  flink_operator_namespace?: string | null
  flink_operator_image?: string | null
  flink_operator_flink_version?: string | null
  flink_operator_runtime_images?: RuntimeImageEntry[]
  flink_operator_service_account?: string | null
  flink_k8s_context?: string | null
  flink_k8s_kubeconfig_path?: string | null
  flink_operator_jm_rest_template?: string | null
  flink_k8s_cluster_domain?: string | null
  flink_operator_checkpoint_dir?: string | null
  flink_operator_image_pull_secrets?: string | null
  flink_operator_s3_auth_mode?: string | null
  flink_operator_s3_access_key_id?: string | null
  flink_operator_s3_secret_configured?: boolean
  flink_operator_s3_region?: string | null
  flink_operator_s3_endpoint_url?: string | null
  flink_operator_jar_s3_prefix?: string | null
  flink_operator_jm_gateway_enabled?: boolean
  flink_operator_jm_gateway_host?: string | null
  flink_operator_jm_gateway_namespace?: string | null
  flink_operator_jm_gateway_ingress_class?: string | null
  flink_operator_jm_gateway_dns_ip?: string | null
  flink_operator_jm_gateway_port?: number | null
  flink_operator_jm_gateway_status?: Record<string, unknown> | null
  effective?: Record<string, unknown>
}

const DEFAULT_FLINK_VERSION_OPTIONS = [
  { label: 'v2_2（Flink 2.2.x）', value: 'v2_2' },
  { label: 'v2_0（Flink 2.0.x）', value: 'v2_0' },
  { label: 'v1_20（Flink 1.20.x）', value: 'v1_20' },
  { label: 'v1_17（Flink 1.17.x）', value: 'v1_17' },
]

const { Text } = Typography

function apiErrorMessage(e: any, fallback: string): string {
  const detail = e?.response?.data?.detail ?? e?.message
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    return detail.map((x: any) => (x?.msg != null ? String(x.msg) : JSON.stringify(x))).join('；')
  }
  return fallback
}

function EffectiveBlock({ effective }: { effective?: Record<string, unknown> }) {
  if (!effective) return <span style={{ color: '#94a3b8' }}>—</span>
  const items = [
    ['namespace', '提交命名空间'],
    ['image', '运行时镜像'],
    ['flink_version', 'CRD 版本'],
    ['service_account', 'ServiceAccount'],
    ['k8s_context', 'Kube Context'],
    ['kubeconfig_path', 'Kubeconfig'],
    ['jm_rest_template', 'JM REST 模板'],
    ['cluster_domain', '集群域名'],
    ['checkpoint_dir', 'Checkpoint'],
    ['image_pull_secrets', 'imagePullSecrets'],
    ['s3_auth_mode', 'S3 认证'],
    ['s3_credentials_configured', 'S3 AK/SK'],
    ['s3_region', 'S3 区域'],
    ['s3_endpoint_url', 'S3 Endpoint'],
    ['jar_s3_prefix', 'JAR 制品 S3'],
  ] as const
  return (
    <Descriptions size="small" column={1} bordered style={{ maxWidth: 720 }}>
      {items.map(([key, label]) => {
        const v = effective[key]
        if (v == null || v === '') return null
        return (
          <Descriptions.Item key={key} label={label}>
            <code style={{ wordBreak: 'break-all', fontSize: 12 }}>
              {typeof v === 'boolean' ? (v ? '已配置' : '未配置') : String(v)}
            </code>
          </Descriptions.Item>
        )
      })}
    </Descriptions>
  )
}

export default function OperatorClustersPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_STREAM_WRITE, currentWorkspace)

  const [list, setList] = useState<OperatorProfile[]>([])
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [submitLoading, setSubmitLoading] = useState(false)
  const [versionOptions, setVersionOptions] = useState(DEFAULT_FLINK_VERSION_OPTIONS)
  const [provisionLoadingId, setProvisionLoadingId] = useState<number | null>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    streamingApi.flinkRuntime().then((r: any) => {
      const opts = r?.supported_operator_flink_versions
      if (Array.isArray(opts) && opts.length) {
        setVersionOptions(
          opts.map((o: { value: string; label: string }) => ({
            value: o.value,
            label: `${o.value}（${o.label}）`,
          })),
        )
      }
    }).catch(() => { /* 使用 DEFAULT_FLINK_VERSION_OPTIONS */ })
  }, [])

  const load = useCallback(async () => {
    if (!wsId) {
      setList([])
      return
    }
    setLoading(true)
    try {
      const rows: any = await streamingApi.listOperatorProfiles(wsId)
      setList(Array.isArray(rows) ? rows : [])
    } catch (e: any) {
      message.error(apiErrorMessage(e, '加载 Operator 集群失败'))
      setList([])
    } finally {
      setLoading(false)
    }
  }, [wsId])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    form.setFieldsValue({
      is_default: false,
      is_enabled: true,
      flink_operator_service_account: 'flink',
      flink_operator_s3_auth_mode: 'static',
      flink_operator_runtime_images: [
        { label: 'Flink 2.2.1', image: '', flink_version: 'v2_2', is_default: true },
      ],
      flink_operator_jm_gateway_enabled: false,
      flink_operator_jm_gateway_port: 8080,
    })
    setDrawerOpen(true)
  }

  const runtimeImagesForForm = (row: OperatorProfile): RuntimeImageEntry[] => {
    const list = row.flink_operator_runtime_images
    if (Array.isArray(list) && list.length) return list
    const img = String(row.flink_operator_image || row.effective?.image || '').trim()
    if (!img) return [{ label: '', image: '', flink_version: 'v2_2', is_default: true }]
    return [{
      label: '',
      image: img,
      flink_version: String(row.flink_operator_flink_version || row.effective?.flink_version || 'v2_2'),
      is_default: true,
    }]
  }

  const openEdit = (row: OperatorProfile) => {
    setEditingId(row.id)
    form.setFieldsValue({
      name: row.name,
      description: row.description ?? '',
      is_default: row.is_default,
      is_enabled: row.is_enabled,
      flink_operator_namespace: row.flink_operator_namespace ?? '',
      flink_operator_runtime_images: runtimeImagesForForm(row),
      flink_operator_service_account: row.flink_operator_service_account ?? '',
      flink_k8s_context: row.flink_k8s_context ?? '',
      flink_k8s_kubeconfig_path: row.flink_k8s_kubeconfig_path ?? '',
      flink_operator_jm_rest_template: row.flink_operator_jm_rest_template ?? '',
      flink_k8s_cluster_domain: row.flink_k8s_cluster_domain ?? '',
      flink_operator_checkpoint_dir: row.flink_operator_checkpoint_dir ?? '',
      flink_operator_image_pull_secrets: row.flink_operator_image_pull_secrets ?? '',
      flink_operator_s3_auth_mode: row.flink_operator_s3_auth_mode ?? row.effective?.s3_auth_mode ?? 'static',
      flink_operator_s3_access_key_id: row.flink_operator_s3_access_key_id ?? '',
      flink_operator_s3_region: row.flink_operator_s3_region ?? row.effective?.s3_region ?? '',
      flink_operator_s3_endpoint_url: row.flink_operator_s3_endpoint_url ?? row.effective?.s3_endpoint_url ?? '',
      flink_operator_jar_s3_prefix: row.flink_operator_jar_s3_prefix ?? row.effective?.jar_s3_prefix ?? '',
      flink_operator_jm_gateway_enabled: Boolean(row.flink_operator_jm_gateway_enabled),
      flink_operator_jm_gateway_host: row.flink_operator_jm_gateway_host ?? '',
      flink_operator_jm_gateway_namespace: row.flink_operator_jm_gateway_namespace ?? '',
      flink_operator_jm_gateway_ingress_class: row.flink_operator_jm_gateway_ingress_class ?? '',
      flink_operator_jm_gateway_dns_ip: row.flink_operator_jm_gateway_dns_ip ?? '',
      flink_operator_jm_gateway_port: row.flink_operator_jm_gateway_port ?? 8080,
    })
    setDrawerOpen(true)
  }

  const normalizePayload = (values: Record<string, unknown>) => {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(values)) {
      if (typeof v === 'string') {
        const s = v.trim()
        out[k] = s === '' ? null : s
      } else {
        out[k] = v
      }
    }
    return out
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const ns = String(values.flink_operator_namespace || '').trim()
      const runtimeRows = (values.flink_operator_runtime_images || []) as RuntimeImageEntry[]
      const runtimeFilled = runtimeRows.filter(r => String(r?.image || '').trim())
      if (!ns && runtimeFilled.length === 0) {
        message.warning('至少填写「提交命名空间」或配置一项「运行时镜像」')
        return
      }
      if (!wsId) {
        message.warning('请先选择工作空间')
        return
      }
      setSubmitLoading(true)
      const payload = normalizePayload(values)
      payload.flink_operator_runtime_images = runtimeFilled.map((r, idx) => ({
        label: String(r.label || '').trim() || undefined,
        image: String(r.image || '').trim(),
        flink_version: String(r.flink_version || '').trim() || undefined,
        is_default: Boolean(r.is_default) || (runtimeFilled.length === 1 && idx === 0),
      }))
      delete payload.flink_operator_image
      delete payload.flink_operator_flink_version
      if (editingId != null && !String(values.flink_operator_s3_secret_access_key || '').trim()) {
        delete payload.flink_operator_s3_secret_access_key
      }
      if (editingId == null) {
        await streamingApi.createOperatorProfile({ ...payload, workspace_id: wsId })
        message.success('Operator 集群已创建')
      } else {
        await streamingApi.updateOperatorProfile(editingId, payload)
        message.success('已保存')
      }
      setDrawerOpen(false)
      setEditingId(null)
      form.resetFields()
      await load()
    } catch (e: any) {
      if (e?.errorFields?.length) return
      message.error(apiErrorMessage(e, '保存失败'))
    } finally {
      setSubmitLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await streamingApi.deleteOperatorProfile(id)
      message.success('已删除')
      load()
    } catch (e: any) {
      message.error(apiErrorMessage(e, '删除失败'))
    }
  }

  const handleProvisionGateway = async (row: OperatorProfile) => {
    setProvisionLoadingId(row.id)
    try {
      await streamingApi.provisionOperatorJmGateway(row.id)
      message.success('JM Ingress 网关已部署/更新')
      await load()
    } catch (e: any) {
      message.error(apiErrorMessage(e, 'JM 网关部署失败'))
    } finally {
      setProvisionLoadingId(null)
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, row: OperatorProfile) => (
        <Space wrap>
          <ClusterOutlined style={{ color: '#6366f1' }} />
          <span>{name}</span>
          {row.is_default ? <Tag color="blue">默认</Tag> : null}
          {!row.is_enabled ? <Tag>已禁用</Tag> : null}
        </Space>
      ),
    },
    {
      title: '命名空间',
      render: (_: unknown, row: OperatorProfile) => (
        <Tooltip title="合并平台默认后的生效值">
          <code>{String(row.effective?.namespace ?? row.flink_operator_namespace ?? '—')}</code>
        </Tooltip>
      ),
    },
    {
      title: '运行时镜像',
      ellipsis: true,
      render: (_: unknown, row: OperatorProfile) => {
        const imgs = row.flink_operator_runtime_images?.length
          ? row.flink_operator_runtime_images
          : (row.flink_operator_image || row.effective?.image
            ? [{ image: String(row.effective?.image ?? row.flink_operator_image), label: '默认' }]
            : [])
        if (!imgs.length) return '—'
        const preview = imgs.map(i => i.label || i.image).join(' · ')
        return (
          <Tooltip title={imgs.map(i => i.image).join('\n')}>
            <span style={{ fontSize: 12 }}>{preview} ({imgs.length})</span>
          </Tooltip>
        )
      },
    },
    {
      title: 'Kube Context',
      dataIndex: 'flink_k8s_context',
      render: (v: string | null, row: OperatorProfile) => (
        <span style={{ fontSize: 12 }}>{v || String(row.effective?.k8s_context ?? '—')}</span>
      ),
    },
    {
      title: 'JM 网关',
      width: 120,
      render: (_: unknown, row: OperatorProfile) => {
        const st = row.flink_operator_jm_gateway_status as { ready?: boolean; host?: string } | null | undefined
        if (!row.flink_operator_jm_gateway_enabled) return <Text type="secondary">未启用</Text>
        if (st?.ready) return <Tag color="success">已就绪</Tag>
        return <Tag color="warning">待部署</Tag>
      },
    },
    {
      title: '操作',
      width: 220,
      render: (_: unknown, row: OperatorProfile) => (
        <Space wrap>
          {canWrite ? (
            <>
              <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
              {row.flink_operator_jm_gateway_enabled ? (
                <Button
                  size="small"
                  loading={provisionLoadingId === row.id}
                  onClick={() => handleProvisionGateway(row)}
                >
                  部署网关
                </Button>
              ) : null}
              <Popconfirm
                title="删除该 Operator 集群？"
                description="仍有作业绑定时无法删除，请先在作业开发中解除绑定。"
                onConfirm={() => handleDelete(row.id)}
              >
                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
            </>
          ) : (
            <Button size="small" onClick={() => openEdit(row)}>查看</Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
        <div>
          <h2 style={{ marginBottom: 8 }}>Operator 集群管理</h2>
          <div style={{ fontSize: 13, color: '#64748b', maxWidth: 720 }}>
            为当前工作空间配置多套 Flink Kubernetes Operator 目标集群（命名空间、kube context 等）。
            每个集群可配置<strong>多个运行时镜像</strong>；作业开发中选择集群后，从该集群已配置的镜像下拉选择。
            各字段说明见仓库文档 <code>gido/docs/OPERATOR_CLUSTER_PROFILE.md</code>。
          </div>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          {canWrite ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增集群</Button>
          ) : null}
        </Space>
      </div>

      {!wsId ? (
        <Alert type="info" message="请先选择工作空间" showIcon />
      ) : (
        <>
          {!canWrite ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="当前账号仅可查看；新增与编辑需 gido:stream:write 权限或空间管理员。"
            />
          ) : null}
          <Table
            loading={loading}
            dataSource={list}
            columns={columns}
            rowKey="id"
            expandable={{
              expandedRowRender: (row: OperatorProfile) => (
                <div style={{ padding: '8px 0' }}>
                  {row.description ? (
                    <p style={{ marginBottom: 12, color: '#64748b' }}>{row.description}</p>
                  ) : null}
                  {row.flink_operator_jm_gateway_status ? (
                    <Alert
                      type={(row.flink_operator_jm_gateway_status as { ready?: boolean }).ready ? 'success' : 'warning'}
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="JM Ingress 网关"
                      description={
                        String((row.flink_operator_jm_gateway_status as { message?: string }).message || '')
                        || JSON.stringify(row.flink_operator_jm_gateway_status)
                      }
                    />
                  ) : null}
                  <div style={{ marginBottom: 8, fontWeight: 500 }}>生效配置（Profile + 平台默认）</div>
                  <EffectiveBlock effective={row.effective} />
                </div>
              ),
            }}
            locale={{ emptyText: '暂无 Operator 集群，点击「新增集群」或沿用平台默认配置' }}
          />
          <div style={{ marginTop: 16, fontSize: 13, color: '#64748b' }}>
            在 <Link to={R.stream.studio}>作业开发</Link> 中为每个实时作业选择 Operator 集群；运行态见{' '}
            <Link to={R.stream.overview}>Flink 运行概览</Link>。
          </div>
        </>
      )}

      <Drawer
        title={editingId == null ? '新增 Operator 集群' : canWrite ? '编辑 Operator 集群' : 'Operator 集群详情'}
        width={560}
        open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setEditingId(null); form.resetFields() }}
        destroyOnClose
        extra={
          canWrite ? (
            <Space>
              <Button onClick={() => { setDrawerOpen(false); setEditingId(null); form.resetFields() }}>取消</Button>
              <Button type="primary" loading={submitLoading} onClick={handleSubmit}>保存</Button>
            </Space>
          ) : null
        }
      >
        <Form form={form} layout="vertical" disabled={!canWrite}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
            extra="工作空间内展示名，仅 GIDO 元数据；下拉与运维中显示。"
          >
            <Input placeholder="如 prod-flink、kind-dev" />
          </Form.Item>
          <Form.Item name="description" label="说明" extra="环境、负责人、集群备注；不参与 K8s 提交。">
            <Input.TextArea rows={2} placeholder="可选，便于区分测试/生产集群" />
          </Form.Item>
          <Space size="large" style={{ marginBottom: 16 }}>
            <Form.Item
              name="is_default"
              label="设为默认"
              valuePropName="checked"
              style={{ marginBottom: 0 }}
              extra="同空间仅一条；新建时自动取消其它默认。"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="is_enabled"
              label="启用"
              valuePropName="checked"
              style={{ marginBottom: 0 }}
              extra="关闭后不出现在下拉且不可绑定作业。"
            >
              <Switch />
            </Form.Item>
          </Space>

          <div style={{ fontWeight: 600, marginBottom: 12 }}>集群与提交</div>
          <Form.Item
            name="flink_operator_namespace"
            label="提交命名空间"
            extra="FlinkDeployment CR 与作业 Pod 所在 ns；Operator watchNamespaces 须包含此 ns。与镜像至少填一项。留空继承 GIDO_FLINK_OPERATOR_NAMESPACE。"
          >
            <Input placeholder="flink" />
          </Form.Item>
          <Form.Item
            name="flink_k8s_context"
            label="Kube Context"
            extra="kubeconfig 中的 context 名；一套 GIDO 管多集群时必填。留空继承 GIDO_FLINK_K8S_CONTEXT。"
          >
            <Input placeholder="如 kind-gido、arn:aws:eks:..." />
          </Form.Item>
          <Form.Item
            name="flink_k8s_kubeconfig_path"
            label="Kubeconfig 路径（Backend 容器内）"
            extra="Backend 进程读到的 kubeconfig 绝对路径；Kind 开发常用 /tmp/kube-for-backend。"
          >
            <Input placeholder="/tmp/kube-for-backend" />
          </Form.Item>
          <Form.Item
            name="flink_k8s_cluster_domain"
            label="集群 DNS 后缀"
            extra="Service FQDN 后缀，默认 cluster.local；非标准集群再改。"
          >
            <Input placeholder="cluster.local" />
          </Form.Item>

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>运行时镜像（可配置多个）</div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="同一 K8s 集群可挂多个 Flink 版本镜像；作业提交时从本列表选择。"
          />
          <Form.List name="flink_operator_runtime_images">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...restField }) => (
                  <div
                    key={key}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '120px 1fr 140px auto auto',
                      gap: 8,
                      alignItems: 'start',
                      marginBottom: 8,
                    }}
                  >
                    <Form.Item
                      {...restField}
                      name={[name, 'label']}
                      label={name === 0 ? '显示名' : undefined}
                      style={{ marginBottom: 0 }}
                    >
                      <Input placeholder="Flink 2.2.1" />
                    </Form.Item>
                    <Form.Item
                      {...restField}
                      name={[name, 'image']}
                      label={name === 0 ? '镜像' : undefined}
                      rules={[{ required: true, message: '请填写镜像' }]}
                      style={{ marginBottom: 0 }}
                    >
                      <Input placeholder="gido-flink-runtime:2.2.1" />
                    </Form.Item>
                    <Form.Item
                      {...restField}
                      name={[name, 'flink_version']}
                      label={name === 0 ? 'CRD 版本' : undefined}
                      style={{ marginBottom: 0 }}
                    >
                      <Select allowClear placeholder="自动推断" options={versionOptions} />
                    </Form.Item>
                    <Form.Item
                      {...restField}
                      name={[name, 'is_default']}
                      label={name === 0 ? '默认' : undefined}
                      valuePropName="checked"
                      style={{ marginBottom: 0 }}
                    >
                      <Switch
                        checkedChildren="默认"
                        onChange={(checked) => {
                          if (!checked) return
                          const rows = form.getFieldValue('flink_operator_runtime_images') || []
                          form.setFieldsValue({
                            flink_operator_runtime_images: rows.map((r: RuntimeImageEntry, idx: number) => ({
                              ...r,
                              is_default: idx === name,
                            })),
                          })
                        }}
                      />
                    </Form.Item>
                    {fields.length > 1 ? (
                      <Button
                        type="text"
                        danger
                        icon={<MinusCircleOutlined />}
                        onClick={() => remove(name)}
                        style={{ marginTop: name === 0 ? 30 : 4 }}
                      />
                    ) : null}
                  </div>
                ))}
                <Button
                  type="dashed"
                  onClick={() => add({ is_default: fields.length === 0, flink_version: 'v2_2' })}
                  block
                  icon={<PlusOutlined />}
                >
                  添加运行时镜像
                </Button>
              </>
            )}
          </Form.List>
          <Form.Item
            name="flink_operator_service_account"
            label="ServiceAccount"
            style={{ marginTop: 16 }}
            extra="作业 Pod 使用的 SA，须在提交命名空间存在（RBAC / IRSA）。默认 flink。"
          >
            <Input placeholder="flink" />
          </Form.Item>
          <Form.Item
            name="flink_operator_image_pull_secrets"
            label="imagePullSecrets"
            extra="逗号分隔 Secret 名，须在提交命名空间已创建（私有镜像）。写入 podTemplate.imagePullSecrets。"
          >
            <Input placeholder="多个 Secret 用英文逗号分隔" />
          </Form.Item>

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>JM Ingress 网关（跨集群 UI）</div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="保存后 GIDO 将在目标集群自动创建 Namespace、nginx、Service、Ingress，并按作业动态转发 *-rest Service。须配置 Kube Context / kubeconfig，且 Backend RBAC 见 k8s/gido-jm-gateway-rbac.yaml。平台须开启 GIDO_FLINK_OPERATOR_UI_PROXY_ENABLED=true。"
          />
          <Form.Item
            name="flink_operator_jm_gateway_enabled"
            label="自动部署 JM Ingress 网关"
            valuePropName="checked"
            extra="启用后保存即部署；JM REST 模板将自动写入网关地址（可覆盖下方手动模板）。"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="flink_operator_jm_gateway_host"
            label="网关 Host"
            extra="Ingress 规则 host，如 jm-gw.flink.cluster-a.internal。留空则使用平台 GIDO_FLINK_OPERATOR_JM_GATEWAY_HOST_SUFFIX 生成。"
          >
            <Input placeholder="jm-gw.flink.cluster-a.internal" />
          </Form.Item>
          <Form.Item
            name="flink_operator_jm_gateway_namespace"
            label="网关安装命名空间"
            extra="默认 gido-flink-gateway（平台 GIDO_FLINK_OPERATOR_JM_GATEWAY_NAMESPACE）。"
          >
            <Input placeholder="gido-flink-gateway" />
          </Form.Item>
          <Form.Item
            name="flink_operator_jm_gateway_ingress_class"
            label="Ingress Class"
            extra="与集群 Ingress Controller 一致，如 nginx、nginx-internal。"
          >
            <Input placeholder="nginx" />
          </Form.Item>
          <Form.Item
            name="flink_operator_jm_gateway_dns_ip"
            label="集群 DNS IP（nginx resolver）"
            extra="CoreDNS/kube-dns 的 ClusterIP，如 10.96.0.10。自动探测失败时必填；查询：kubectl -n kube-system get svc kube-dns coredns"
          >
            <Input placeholder="10.96.0.10" />
          </Form.Item>
          <Form.Item
            name="flink_operator_jm_gateway_port"
            label="网关端口"
            extra="gido-jm-gw nginx/Service/Ingress backend 端口（默认 8080）。防火墙仅开放 8081 时填 8081；`jm_rest_template` 会同步写入对应 URL 端口。"
          >
            <InputNumber min={1} max={65535} placeholder="8080" style={{ width: '100%' }} />
          </Form.Item>

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>高级</div>
          <Form.Item
            name="flink_operator_jm_rest_template"
            label="JM REST 模板"
            extra="Backend 查状态/取消/UI 用的 JM REST URL。启用网关后通常自动填充；占位符 {deployment_name}、{namespace}。"
          >
            <Input placeholder="http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081" />
          </Form.Item>
          <Form.Item
            name="flink_operator_checkpoint_dir"
            label="Checkpoint 目录"
            extra="写入 state.checkpoints.dir；EKS 常用 s3://…/flink-checkpoints，须 Pod 有 S3 权限。"
          >
            <Input placeholder="s3://bucket/flink-checkpoints 或 file:///..." />
          </Form.Item>

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>S3 认证</div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="各 Operator 集群可配置独立 S3：SQL 制品 / checkpoint 与 AK/SK。JAR 仅存 backend 本地并经 HTTP 拉取，不再使用 JAR 制品 S3 前缀。"
          />
          <Form.Item
            name="flink_operator_jar_s3_prefix"
            label="SQL 制品 S3 前缀（JAR 不使用）"
            extra="仅 SQL 制品同步 S3；JAR 上传至 backend 本地 PVC，Operator 经 FLINK_OPERATOR_JAR_HTTP_BASE HTTP 拉取。留空沿用平台 FLINK_OPERATOR_JAR_S3_PREFIX（SQL）。"
          >
            <Input placeholder="s3://your-bucket/cluster-a/jars" />
          </Form.Item>
          <Form.Item
            name="flink_operator_s3_region"
            label="S3 区域"
            extra="AWS 区域，如 ap-southeast-1；留空沿用平台 GIDO_ARTIFACT_S3_REGION。"
          >
            <Input placeholder="ap-southeast-1" />
          </Form.Item>
          <Form.Item
            name="flink_operator_s3_endpoint_url"
            label="S3 Endpoint"
            extra="如 https://s3.ap-southeast-1.amazonaws.com；MinIO/兼容存储必填；留空沿用平台 GIDO_ARTIFACT_S3_ENDPOINT_URL。"
          >
            <Input placeholder="https://s3.ap-southeast-1.amazonaws.com" />
          </Form.Item>
          <Form.Item
            name="flink_operator_s3_auth_mode"
            label="认证方式"
            extra="static：本集群 AK/SK；irsa：Pod IRSA（EKS）。"
          >
            <Select
              options={[
                { label: 'static（AK/SK）', value: 'static' },
                { label: 'irsa（IAM Role）', value: 'irsa' },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.flink_operator_s3_auth_mode !== cur.flink_operator_s3_auth_mode}
          >
            {({ getFieldValue }) =>
              getFieldValue('flink_operator_s3_auth_mode') === 'static' ? (
                <>
                  <Form.Item
                    name="flink_operator_s3_access_key_id"
                    label="Access Key ID"
                    rules={[{ required: true, message: '请填写 Access Key ID' }]}
                  >
                    <Input placeholder="AKIA..." autoComplete="off" />
                  </Form.Item>
                  <Form.Item
                    name="flink_operator_s3_secret_access_key"
                    label="Secret Access Key"
                    extra={
                      editingId != null
                        ? '留空则保留原 Secret，不会清空。'
                        : '提交后不在列表中回显，请妥善保管。'
                    }
                    rules={editingId == null ? [{ required: true, message: '请填写 Secret Access Key' }] : []}
                  >
                    <Input.Password placeholder="Secret Key" autoComplete="new-password" />
                  </Form.Item>
                  <Form.Item
                    name="flink_operator_s3_session_token"
                    label="Session Token（可选）"
                  >
                    <Input.Password placeholder="临时凭证 Token" autoComplete="new-password" />
                  </Form.Item>
                </>
              ) : null
            }
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  )
}
