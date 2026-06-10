/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Alert, Input, Modal } from 'antd'

type Props = {
  open: boolean
  title: string
  hint?: string
  note: string
  onNoteChange: (v: string) => void
  onCancel: () => void
  onSubmit: () => void
  loading?: boolean
}

export default function PublishApprovalModal({
  open,
  title,
  hint = '普通开发不能直接发布到生产。提交后由空间/平台管理员审批，通过后将自动执行发布动作。',
  note,
  onNoteChange,
  onCancel,
  onSubmit,
  loading,
}: Props) {
  return (
    <Modal
      title={title}
      open={open}
      onOk={onSubmit}
      onCancel={onCancel}
      okText="提交审批"
      confirmLoading={loading}
    >
      <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={hint} />
      <Input.TextArea
        rows={3}
        placeholder="变更说明（可选）"
        value={note}
        onChange={e => onNoteChange(e.target.value)}
      />
    </Modal>
  )
}
