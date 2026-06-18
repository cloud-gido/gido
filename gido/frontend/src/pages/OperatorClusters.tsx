/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Form, Input, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, message,
} from 'antd'
import { ClusterOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { R } from '../routes'

type OperatorProfile = {
  id: number
  name: string
  description?: string | null
  is_default: boolean
  is_enabled: boolean
  flink_operator_namespace?: string | null
  flink_operator_image?: string | null
  flink_operator_flink_version?: string | null
  flink_operator_service_account?: string | null
  flink_k8s_context?: string | null
  flink_k8s_kubeconfig_path?: string | null
  flink_operator_jm_rest_template?: string | null
  flink_k8s_cluster_domain?: string | null
  flink_operator_checkpoint_dir?: string | null
  flink_operator_image_pull_secrets?: string | null
  effective?: Record<string, unknown>
}

const DEFAULT_FLINK_VERSION_OPTIONS = [
  { label: 'v2_2（Flink 2.2.x）', value: 'v2_2' },
  { label: 'v2_0（Flink 2.0.x）', value: 'v2_0' },
  { label: 'v1_20（Flink 1.20.x）', value: 'v1_20' },
  { label: 'v1_17（Flink 1.17.x）', value: 'v1_17' },
]

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
  ] as const
  return (
    <Descriptions size="small" column={1} bordered style={{ maxWidth: 720 }}>
      {items.map(([key, label]) => {
        const v = effective[key]
        if (v == null || v === '') return null
        return (
          <Descriptions.Item key={key} label={label}>
            <code style={{ wordBreak: 'break-all', fontSize: 12 }}>{String(v)}</code>
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
      flink_operator_flink_version: 'v2_2',
      flink_operator_service_account: 'flink',
    })
    setDrawerOpen(true)
  }

  const openEdit = (row: OperatorProfile) => {
    setEditingId(row.id)
    form.setFieldsValue({
      name: row.name,
      description: row.description ?? '',
      is_default: row.is_default,
      is_enabled: row.is_enabled,
      flink_operator_namespace: row.flink_operator_namespace ?? '',
      flink_operator_image: row.flink_operator_image ?? '',
      flink_operator_flink_version: row.flink_operator_flink_version ?? row.effective?.flink_version ?? '',
      flink_operator_service_account: row.flink_operator_service_account ?? '',
      flink_k8s_context: row.flink_k8s_context ?? '',
      flink_k8s_kubeconfig_path: row.flink_k8s_kubeconfig_path ?? '',
      flink_operator_jm_rest_template: row.flink_operator_jm_rest_template ?? '',
      flink_k8s_cluster_domain: row.flink_k8s_cluster_domain ?? '',
      flink_operator_checkpoint_dir: row.flink_operator_checkpoint_dir ?? '',
      flink_operator_image_pull_secrets: row.flink_operator_image_pull_secrets ?? '',
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
      const img = String(values.flink_operator_image || '').trim()
      if (!ns && !img) {
        message.warning('至少填写「提交命名空间」或「运行时镜像」之一')
        return
      }
      if (!wsId) {
        message.warning('请先选择工作空间')
        return
      }
      setSubmitLoading(true)
      const payload = normalizePayload(values)
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
      render: (_: unknown, row: OperatorProfile) => (
        <Tooltip title={String(row.effective?.image ?? row.flink_operator_image ?? '')}>
          <span style={{ fontSize: 12 }}>
            {String(row.effective?.image ?? row.flink_operator_image ?? '—')}
          </span>
        </Tooltip>
      ),
    },
    {
      title: 'Kube Context',
      dataIndex: 'flink_k8s_context',
      render: (v: string | null, row: OperatorProfile) => (
        <span style={{ fontSize: 12 }}>{v || String(row.effective?.k8s_context ?? '—')}</span>
      ),
    },
    {
      title: '操作',
      width: 160,
      render: (_: unknown, row: OperatorProfile) => (
        <Space>
          {canWrite ? (
            <>
              <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
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
            为当前工作空间配置多套 Flink Kubernetes Operator 目标集群（命名空间、镜像、kube context 等）。
            未填写的项将继承平台 <code>.env</code> 默认值；作业开发中可选择集群并覆盖运行时镜像。
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

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>运行时</div>
          <Form.Item
            name="flink_operator_image"
            label="运行时镜像"
            extra="FlinkDeployment.spec.image；生产常用 gido-flink-runtime。留空继承 GIDO_FLINK_OPERATOR_IMAGE。作业级「运行时镜像」可再覆盖。"
          >
            <Input placeholder="apache/flink:2.2.1-java11、1.17.2-java11 或私有运行时镜像" />
          </Form.Item>
          <Form.Item
            name="flink_operator_flink_version"
            label="Operator CRD 版本"
            extra="须与集群 FlinkDeployment CRD 一致；1.17.2 镜像用 v1_17，2.2.x 用 v2_2。仅填镜像时可自动推断。"
          >
            <Select allowClear placeholder="留空则继承平台默认或按镜像推断" options={versionOptions} />
          </Form.Item>
          <Form.Item
            name="flink_operator_service_account"
            label="ServiceAccount"
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

          <div style={{ fontWeight: 600, margin: '16px 0 12px' }}>高级</div>
          <Form.Item
            name="flink_operator_jm_rest_template"
            label="JM REST 模板"
            extra="Backend 查状态/取消/UI 用的 JM REST URL。占位符 {deployment_name}、{namespace}。同集群默认集群 DNS。"
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
        </Form>
      </Drawer>
    </div>
  )
}
