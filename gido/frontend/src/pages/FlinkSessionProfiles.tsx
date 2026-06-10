/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Modal, Form, Input, Space, message, Alert, Collapse, Typography, Descriptions, Card, Tag,
} from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, CopyOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, isPlatformAdmin, P } from '../perm'
import { R } from '../routes'

const { Paragraph, Text } = Typography

const OVERRIDE_KEYS = [
  'flink_url',
  'flink_sql_gateway_url',
  'flink_gateway_jobmanager_rest_url',
  'flink_ui_url',
  'flink_k8s_application_image',
  'flink_k8s_namespace',
  'flink_k8s_application_jm_rest_template',
  'flink_k8s_cluster_domain',
  'flink_k8s_apiserver_fallback_url',
  'flink_k8s_jm_rpc_host',
  'flink_k8s_sql_gateway_rest_host',
] as const

function countOverrides(row: Record<string, unknown>) {
  let n = 0
  for (const k of OVERRIDE_KEYS) {
    const v = row[k]
    if (v != null && String(v).trim()) n += 1
  }
  return n
}

/** 工作空间「命名集群连接」：在租户平台默认之上覆写，对齐云厂商「默认连接 + 项目连接」分层 */
export default function FlinkSessionProfilesPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canRead = can(user, P.GIDO_STREAM_READ, currentWorkspace)
  const canManage =
    (currentWorkspace?.my_role === 'admin' || isPlatformAdmin(user)) && can(user, P.GIDO_STREAM_WRITE, currentWorkspace)

  const [list, setList] = useState<any[]>([])
  const [defaultsMeta, setDefaultsMeta] = useState<any>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form] = Form.useForm()

  const loadAll = useCallback(async () => {
    if (!wsId || !canRead) return
    try {
      const [rows, def]: any = await Promise.all([
        streamingApi.listFlinkSessionProfiles(wsId),
        streamingApi.flinkPlatformDefaults(wsId),
      ])
      setList(Array.isArray(rows) ? rows : [])
      setDefaultsMeta(def && typeof def === 'object' ? def : null)
    } catch {
      setList([])
      setDefaultsMeta(null)
      message.error('加载失败')
    }
  }, [wsId, canRead])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (row: any) => {
    setEditingId(row.id)
    form.setFieldsValue({
      name: row.name,
      flink_url: row.flink_url ?? '',
      flink_sql_gateway_url: row.flink_sql_gateway_url ?? '',
      flink_gateway_jobmanager_rest_url: row.flink_gateway_jobmanager_rest_url ?? '',
      flink_ui_url: row.flink_ui_url ?? '',
      flink_k8s_application_image: row.flink_k8s_application_image ?? '',
      flink_k8s_namespace: row.flink_k8s_namespace ?? '',
      flink_k8s_application_jm_rest_template: row.flink_k8s_application_jm_rest_template ?? '',
      flink_k8s_cluster_domain: row.flink_k8s_cluster_domain ?? '',
      flink_k8s_apiserver_fallback_url: row.flink_k8s_apiserver_fallback_url ?? '',
      flink_k8s_jm_rpc_host: row.flink_k8s_jm_rpc_host ?? '',
      flink_k8s_sql_gateway_rest_host: row.flink_k8s_sql_gateway_rest_host ?? '',
    })
    setModalOpen(true)
  }

  const fillFormFromPlatformDefaults = () => {
    const e = defaultsMeta?.effective
    if (!e || typeof e !== 'object') {
      message.warning('尚未加载到平台默认，请稍后重试')
      return
    }
    const next: Record<string, string> = {}
    for (const k of OVERRIDE_KEYS) {
      next[k] = e[k] != null ? String(e[k]) : ''
    }
    form.setFieldsValue(next)
    message.info('已填入当前平台合并默认值；请删去不需覆写的项（留空=继承），并至少保留一项与默认不同的值以满足保存规则')
  }

  const norm = (v: unknown) => {
    const s = v == null ? '' : String(v).trim()
    return s === '' ? undefined : s
  }

  const handleSubmit = async () => {
    if (!wsId) return
    const values = await form.validateFields()
    const payload = {
      name: String(values.name).trim(),
      flink_url: norm(values.flink_url),
      flink_sql_gateway_url: norm(values.flink_sql_gateway_url),
      flink_gateway_jobmanager_rest_url: norm(values.flink_gateway_jobmanager_rest_url),
      flink_ui_url: norm(values.flink_ui_url),
      flink_k8s_application_image: norm(values.flink_k8s_application_image),
      flink_k8s_namespace: norm(values.flink_k8s_namespace),
      flink_k8s_application_jm_rest_template: norm(values.flink_k8s_application_jm_rest_template),
      flink_k8s_cluster_domain: norm(values.flink_k8s_cluster_domain),
      flink_k8s_apiserver_fallback_url: norm(values.flink_k8s_apiserver_fallback_url),
      flink_k8s_jm_rpc_host: norm(values.flink_k8s_jm_rpc_host),
      flink_k8s_sql_gateway_rest_host: norm(values.flink_k8s_sql_gateway_rest_host),
    }
    try {
      if (editingId == null) {
        await streamingApi.createFlinkSessionProfile({ workspace_id: wsId, ...payload })
        message.success('已创建')
      } else {
        await streamingApi.updateFlinkSessionProfile(editingId, payload)
        message.success('已保存')
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
      return
    }
    setModalOpen(false)
    setEditingId(null)
    form.resetFields()
    loadAll()
  }

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '删除该 Flink 集群连接？',
      content: '若有实时作业仍绑定此连接，删除将被拒绝。',
      okType: 'danger',
      onOk: async () => {
        try {
          await streamingApi.deleteFlinkSessionProfile(id)
          message.success('已删除')
          loadAll()
        } catch (e: any) {
          message.error(e?.response?.data?.detail || '删除失败')
          throw e
        }
      },
    })
  }

  const short = (s: string | null | undefined, n = 36) => {
    if (!s) return '—'
    return s.length <= n ? s : `${s.slice(0, n)}…`
  }

  const eff = defaultsMeta?.effective || {}

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 64 },
    { title: '名称', dataIndex: 'name', width: 160 },
    {
      title: '覆写项数',
      width: 96,
      render: (_: unknown, row: any) => <Tag>{countOverrides(row)}</Tag>,
    },
    { title: 'JobManager', dataIndex: 'flink_url', ellipsis: true, render: (t: string) => short(t, 48) },
    { title: 'SQL Gateway', dataIndex: 'flink_sql_gateway_url', ellipsis: true, render: (t: string) => short(t, 48) },
    {
      title: '操作',
      width: 200,
      render: (_: unknown, row: any) => (
        <Space>
          {canManage && <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>}
          {canManage && (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(row.id)}>删除</Button>
          )}
        </Space>
      ),
    },
  ]

  if (!canRead) {
    return (
      <Alert
        type="warning"
        showIcon
        message="无权限"
        description="需要 gido:stream:read 权限查看 Flink 集群连接。"
      />
    )
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ marginBottom: 8 }}>Flink 集群连接</h2>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 960 }}>
            对标成熟产品：<strong>系统管理 → 集成</strong>里的 Flink 为<strong>租户级默认</strong>（环境变量 + 单行库表合并）；
            此处为<strong>工作空间命名连接</strong>，仅在<strong>与默认不同的字段</strong>上覆写（留空=继承上一层），用于多套物理集群（风控 / 对账等）。
            作业在 <Link to={R.stream.studio}>作业开发</Link> 中选「默认（平台）」或某命名连接后保存即可。
          </Paragraph>
        </div>
        {canManage && (
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建连接</Button>
        )}
      </div>

      <Card size="small" title="当前平台默认（只读，供对照）" style={{ marginBottom: 16 }}>
        {Array.isArray(defaultsMeta?.merge_layers) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="合并顺序"
            description={(
              <ol style={{ margin: '8px 0 0 18px', padding: 0 }}>
                {defaultsMeta.merge_layers.map((s: string, i: number) => (
                  <li key={i} style={{ marginBottom: 4 }}>{s}</li>
                ))}
              </ol>
            )}
          />
        )}
        {defaultsMeta?.job_rule && (
          <Paragraph type="secondary" style={{ marginBottom: 12, fontSize: 13 }}>{defaultsMeta.job_rule}</Paragraph>
        )}
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="JobManager">{short(eff.flink_url, 80)}</Descriptions.Item>
          <Descriptions.Item label="SQL Gateway">{short(eff.flink_sql_gateway_url, 80)}</Descriptions.Item>
          <Descriptions.Item label="Gateway→JM">{short(eff.flink_gateway_jobmanager_rest_url, 80)}</Descriptions.Item>
          <Descriptions.Item label="Flink UI">{short(eff.flink_ui_url, 80)}</Descriptions.Item>
          <Descriptions.Item label="K8s 作业镜像">{short(eff.flink_k8s_application_image, 80)}</Descriptions.Item>
        </Descriptions>
        <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}>
          修改默认值请到 <Link to={R.batch.systemIntegration}>系统管理 → 平台集成 → Flink</Link>（需集成写权限）。
        </Paragraph>
      </Card>

      {!canManage && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="新建、编辑、删除：需当前工作空间「空间管理员」或平台管理员，且具备 gido:stream:write。"
        />
      )}
      <Table dataSource={list} columns={columns} rowKey="id" pagination={false} />

      <Modal
        title={editingId == null ? '新建命名集群连接' : `编辑连接 #${editingId}`}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => { setModalOpen(false); setEditingId(null); form.resetFields() }}
        destroyOnClose
        width={720}
        styles={{ body: { maxHeight: '72vh', overflowY: 'auto' } }}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="勿整套复制平台默认"
          description="至少填写一项与上表「平台默认」不同的地址或 K8s 项；否则无需建连接。留空的字段会继续继承平台默认。"
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="连接名称"
            rules={[{ required: true, message: '例如「风控 Flink」「对账 Session」' }]}
            extra="用于作业下拉展示；与平台集成里的 Flink 不是两套配置，而是继承 + 覆写。"
          >
            <Input placeholder="风控 Flink / 对账 Session" />
          </Form.Item>
          <Button type="link" size="small" icon={<CopyOutlined />} onClick={fillFormFromPlatformDefaults} style={{ paddingLeft: 0, marginBottom: 8 }}>
            将平台当前默认值填入下方（再删掉不需覆写的行即可）
          </Button>
          <Collapse
            defaultActiveKey={['core']}
            items={[
              {
                key: 'core',
                label: 'REST / Gateway / UI（留空=继承平台默认）',
                children: (
                  <>
                    <Form.Item name="flink_url" label="FLINK_URL（JobManager REST）" extra="仅当与平台默认不同时填写">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_sql_gateway_url" label="FLINK_SQL_GATEWAY_URL" extra="须为 Gateway 的 /v1 根">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item
                      name="flink_gateway_jobmanager_rest_url"
                      label="FLINK_GATEWAY_JOBMANAGER_REST_URL"
                      extra="Gateway 进程内需能访问的 JM REST"
                    >
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_ui_url" label="FLINK_UI_URL" extra="浏览器打开 Flink UI">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'k8s',
                label: 'K8s Application（可选，留空=继承平台默认）',
                children: (
                  <>
                    <Form.Item name="flink_k8s_application_image" label="作业镜像">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_namespace" label="命名空间">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_application_jm_rest_template" label="JM REST 模板（含 {cluster_id}）">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_cluster_domain" label="集群 DNS 域">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_apiserver_fallback_url" label="Apiserver 回退 URL">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_jm_rpc_host" label="JM RPC Host">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                    <Form.Item name="flink_k8s_sql_gateway_rest_host" label="SQL Gateway REST Host">
                      <Input placeholder="留空继承" />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
        </Form>
      </Modal>
    </div>
  )
}
