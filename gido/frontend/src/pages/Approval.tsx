/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useState } from 'react'
import {
  Table, Tag, Button, Space, Tabs, message, Modal, Input, Badge, Alert, Typography,
} from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined, RollbackOutlined } from '@ant-design/icons'
import { approvalApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import { isWorkspaceAdmin } from '../perm'
import { APPROVAL_ACTION_LABEL, APPROVAL_RESOURCE_LABEL } from '../approvalLabels'

const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  approved: 'green',
  rejected: 'red',
  cancelled: 'default',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
  cancelled: '已撤回',
}

export default function ApprovalPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const canReview = isWorkspaceAdmin(user, currentWorkspace)
  const [tab, setTab] = useState('pending')
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [canReviewFlag, setCanReviewFlag] = useState(canReview)
  const [reviewModal, setReviewModal] = useState<{ id: number; action: 'approve' | 'reject' } | null>(null)
  const [reviewNote, setReviewNote] = useState('')

  const load = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const status = tab === 'all' ? undefined : tab
      const res: any = await approvalApi.list(wsId, {
        status,
        mine_only: !canReview,
        page,
        page_size: 20,
      })
      setItems(res.items || [])
      setTotal(res.total || 0)
      setCanReviewFlag(!!res.can_review)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setPage(1)
  }, [tab, wsId])

  useEffect(() => {
    load()
  }, [wsId, tab, page])

  const openReview = (id: number, action: 'approve' | 'reject') => {
    setReviewNote('')
    setReviewModal({ id, action })
  }

  const submitReview = async () => {
    if (!reviewModal) return
    try {
      if (reviewModal.action === 'approve') {
        await approvalApi.approve(reviewModal.id, reviewNote || undefined)
        message.success('审批通过，已发布到生产')
      } else {
        await approvalApi.reject(reviewModal.id, reviewNote || undefined)
        message.success('已驳回')
      }
      setReviewModal(null)
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作失败')
    }
  }

  const handleCancel = async (id: number) => {
    try {
      await approvalApi.cancel(id)
      message.success('已撤回')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '撤回失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 64 },
    {
      title: '类型',
      dataIndex: 'resource_type',
      width: 100,
      render: (v: string) => APPROVAL_RESOURCE_LABEL[v] || v,
    },
    { title: '资源', dataIndex: 'resource_name', ellipsis: true },
    {
      title: '发布动作',
      dataIndex: 'action',
      width: 180,
      render: (v: string) => APPROVAL_ACTION_LABEL[v] || v,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 96,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] || s}</Tag>,
    },
    { title: '提交人', dataIndex: 'submitted_by_username', width: 100 },
    {
      title: '提交时间',
      dataIndex: 'submitted_at',
      width: 160,
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '审批人',
      dataIndex: 'reviewed_by_username',
      width: 100,
      render: (v: string) => v || '—',
    },
    {
      title: '说明',
      dataIndex: 'submit_note',
      ellipsis: true,
      render: (v: string, row: any) => v || row.review_note || '—',
    },
    {
      title: '操作',
      width: 200,
      render: (_: unknown, row: any) => (
        <Space size="small">
          {canReviewFlag && row.status === 'pending' && (
            <>
              <Button
                type="primary"
                size="small"
                icon={<CheckOutlined />}
                onClick={() => openReview(row.id, 'approve')}
              >
                通过
              </Button>
              <Button
                danger
                size="small"
                icon={<CloseOutlined />}
                onClick={() => openReview(row.id, 'reject')}
              >
                驳回
              </Button>
            </>
          )}
          {row.status === 'pending' && !canReviewFlag && (
            <Button size="small" icon={<RollbackOutlined />} onClick={() => handleCancel(row.id)}>
              撤回
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const tabItems = [
    { key: 'pending', label: canReviewFlag ? <Badge count={total && tab === 'pending' ? total : 0} offset={[8, 0]}>待审批</Badge> : '待审批' },
    { key: 'approved', label: '已通过' },
    { key: 'rejected', label: '已驳回' },
    { key: 'all', label: '全部' },
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>发布审批</Typography.Title>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={
          canReviewFlag
            ? '您是空间/平台管理员，可审批开发提交的发布申请；通过后系统将自动执行：Dolphin 发布、脚本锁定、Flink 提交、API 上线/下线等。'
            : '普通开发提交发布申请后，需空间管理员或平台管理员审批通过，才会发布到生产环境（含 GIDO Batch / Stream / Serve）。'
        }
      />
      <Tabs activeKey={tab} onChange={k => setTab(k)} items={tabItems} />
      <div style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
        scroll={{ x: 1200 }}
      />

      <Modal
        title={reviewModal?.action === 'approve' ? '审批通过并发布' : '驳回申请'}
        open={!!reviewModal}
        onCancel={() => setReviewModal(null)}
        onOk={submitReview}
        okText={reviewModal?.action === 'approve' ? '通过并发布' : '确认驳回'}
        okButtonProps={{ danger: reviewModal?.action === 'reject' }}
      >
        <Input.TextArea
          rows={3}
          placeholder="审批意见（可选）"
          value={reviewNote}
          onChange={e => setReviewNote(e.target.value)}
        />
      </Modal>
    </div>
  )
}
