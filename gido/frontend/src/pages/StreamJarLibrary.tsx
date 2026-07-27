/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Drawer, Form, Input, Modal, Space, Table, Tag, Typography, Upload, message,
} from 'antd'
import { PlusOutlined, ReloadOutlined, UploadOutlined, DeleteOutlined } from '@ant-design/icons'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'

const { Paragraph, Text } = Typography

export default function StreamJarLibraryPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<any[]>([])
  const [detail, setDetail] = useState<any | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm()
  const [uploadNote, setUploadNote] = useState('')
  const [uploadMainClass, setUploadMainClass] = useState('')

  const load = useCallback(async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const list: any = await streamingApi.listJarArtifacts(wsId)
      setItems(list || [])
    } finally {
      setLoading(false)
    }
  }, [wsId])

  useEffect(() => { void load() }, [load])

  const openDetail = async (id: number) => {
    const d: any = await streamingApi.getJarArtifact(id)
    setDetail(d)
    setDetailOpen(true)
  }

  const handleCreate = async () => {
    if (!wsId) return
    const v = await createForm.validateFields()
    await streamingApi.createJarArtifact({
      workspace_id: wsId,
      name: v.name,
      description: v.description,
    })
    message.success('已创建')
    setCreateOpen(false)
    createForm.resetFields()
    await load()
  }

  const handleDelete = (row: any) => {
    Modal.confirm({
      title: `删除「${row.name}」？`,
      content: row.ref_job_count ? `仍有 ${row.ref_job_count} 个作业引用，无法删除` : '将删除全部版本文件元数据',
      okButtonProps: { danger: true, disabled: Boolean(row.ref_job_count) },
      onOk: async () => {
        await streamingApi.deleteJarArtifact(row.id)
        message.success('已删除')
        await load()
      },
    })
  }

  const handleBackfill = async () => {
    if (!wsId) return
    const r: any = await streamingApi.backfillJarArtifacts(wsId)
    message.success(`已迁移 ${r?.migrated ?? 0} 个作业本地 JAR`)
    await load()
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '最新版本',
      key: 'ver',
      width: 100,
      render: (_: any, row: any) => row.latest_version ? `v${row.latest_version.version}` : '—',
    },
    {
      title: '更新人',
      key: 'user',
      width: 120,
      render: (_: any, row: any) => row.latest_version?.uploaded_by_username || row.owner_username || '—',
    },
    {
      title: '更新时间',
      key: 'at',
      width: 180,
      render: (_: any, row: any) => {
        const t = row.latest_version?.uploaded_at || row.updated_at
        return t ? formatInTimeZone(t, displayTz) : '—'
      },
    },
    {
      title: '引用作业',
      dataIndex: 'ref_job_count',
      key: 'refs',
      width: 90,
    },
    {
      title: '操作',
      key: 'op',
      width: 160,
      render: (_: any, row: any) => (
        <Space>
          <Button type="link" size="small" onClick={() => void openDetail(row.id)}>详情</Button>
          <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(row)} />
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>JAR 包</Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 12, maxWidth: 900 }}>
        资源管理 · JAR：上传与版本审计。在「作业开发」中绑定版本后部署上线；启停与运行态在「作业运维」。
      </Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建 JAR</Button>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>
        <Button onClick={() => void handleBackfill()}>迁移作业本地 JAR</Button>
      </Space>
      <Card>
        <Table rowKey="id" loading={loading} dataSource={items} columns={columns} pagination={false} />
      </Card>

      <Modal title="新建 JAR 包" open={createOpen} onOk={() => void handleCreate()} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={createForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如 football-risk-engine" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={detail ? `JAR · ${detail.name}` : 'JAR 详情'}
        width={640}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        destroyOnClose
      >
        {detail && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={detail.description || '无说明'}
              description={`引用作业 ${detail.ref_job_count ?? 0} · 负责人 ${detail.owner_username || '—'}`}
            />
            <Space wrap>
              <Input
                style={{ width: 220 }}
                placeholder="变更说明（可选）"
                value={uploadNote}
                onChange={e => setUploadNote(e.target.value)}
              />
              <Input
                style={{ width: 260 }}
                placeholder="默认 Main Class（可选）"
                value={uploadMainClass}
                onChange={e => setUploadMainClass(e.target.value)}
              />
              <Upload
                accept=".jar"
                showUploadList={false}
                customRequest={async ({ file, onSuccess, onError }) => {
                  try {
                    await streamingApi.uploadJarVersion(detail.id, file as File, {
                      change_note: uploadNote || undefined,
                      default_main_class: uploadMainClass || undefined,
                    })
                    message.success('已上传新版本')
                    setUploadNote('')
                    const d: any = await streamingApi.getJarArtifact(detail.id)
                    setDetail(d)
                    await load()
                    onSuccess?.({})
                  } catch (e: any) {
                    message.error(e?.response?.data?.detail || '上传失败')
                    onError?.(e)
                  }
                }}
              >
                <Button type="primary" icon={<UploadOutlined />}>上传新版本</Button>
              </Upload>
            </Space>
            <Table
              size="small"
              rowKey="id"
              pagination={false}
              dataSource={detail.versions || []}
              columns={[
                { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => `v${v}` },
                { title: '文件', dataIndex: 'file_name', ellipsis: true },
                {
                  title: '大小',
                  dataIndex: 'size_bytes',
                  width: 90,
                  render: (n: number) => (n != null ? `${Math.round(n / 1024)} KB` : '—'),
                },
                {
                  title: 'SHA256',
                  dataIndex: 'sha256',
                  ellipsis: true,
                  render: (s: string) => s ? <Text code style={{ fontSize: 11 }}>{s.slice(0, 12)}…</Text> : '—',
                },
                { title: '上传人', dataIndex: 'uploaded_by_username', width: 100 },
                {
                  title: '时间',
                  dataIndex: 'uploaded_at',
                  width: 160,
                  render: (t: string) => (t ? formatInTimeZone(t, displayTz) : '—'),
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 90,
                  render: (s: string) => <Tag color={s === 'active' ? 'green' : 'default'}>{s}</Tag>,
                },
                {
                  title: '操作',
                  key: 'op',
                  width: 90,
                  render: (_: any, row: any) => (
                    <Button
                      type="link"
                      size="small"
                      disabled={row.status !== 'active'}
                      onClick={async () => {
                        try {
                          await streamingApi.deprecateJarVersion(row.id)
                          message.success('已废弃')
                          const d: any = await streamingApi.getJarArtifact(detail.id)
                          setDetail(d)
                          await load()
                        } catch (e: any) {
                          message.error(e?.response?.data?.detail || '废弃失败')
                        }
                      }}
                    >
                      废弃
                    </Button>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </div>
  )
}
