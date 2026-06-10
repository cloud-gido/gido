/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Form, Input, Modal, Select, Typography } from 'antd'

const { Text } = Typography

const TIMEZONES = [
  { label: 'UTC+8 东八区 (Asia/Shanghai)', value: 'Asia/Shanghai' },
  { label: 'UTC+9 东九区 (Asia/Tokyo)', value: 'Asia/Tokyo' },
  { label: 'UTC+0 (UTC)', value: 'UTC' },
  { label: 'UTC-5 (America/New_York)', value: 'America/New_York' },
  { label: 'UTC-8 (America/Los_Angeles)', value: 'America/Los_Angeles' },
  { label: 'UTC+1 (Europe/London)', value: 'Europe/London' },
  { label: 'UTC+8 (Asia/Singapore)', value: 'Asia/Singapore' },
]

type Props = {
  tzModal: boolean
  setTzModal: (v: boolean) => void
  tzForm: ReturnType<typeof Form.useForm>[0]
  handleSaveTz: () => Promise<void>
  createWsOpen: boolean
  setCreateWsOpen: (v: boolean) => void
  wsForm: ReturnType<typeof Form.useForm>[0]
  submitNewWorkspace: () => Promise<void>
  tzHint?: string
}

export default function WorkspaceShellModals({
  tzModal,
  setTzModal,
  tzForm,
  handleSaveTz,
  createWsOpen,
  setCreateWsOpen,
  wsForm,
  submitNewWorkspace,
  tzHint,
}: Props) {
  return (
    <>
      <Modal title="工作空间时区" open={tzModal} onOk={handleSaveTz} onCancel={() => setTzModal(false)} width={400} destroyOnClose>
        <Form form={tzForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="timezone" label="时区" rules={[{ required: true }]}>
            <Select options={TIMEZONES} />
          </Form.Item>
          {tzHint ? <Text type="secondary" style={{ fontSize: 12 }}>{tzHint}</Text> : null}
        </Form>
      </Modal>

      <Modal
        title="新建工作空间"
        open={createWsOpen}
        onOk={submitNewWorkspace}
        onCancel={() => { setCreateWsOpen(false); wsForm.resetFields() }}
        okText="创建"
        destroyOnClose
        width={440}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
          仅平台管理员可创建；创建者为负责人，空间角色见成员表。
        </Text>
        <Form form={wsForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写空间名称' }]}>
            <Input placeholder="唯一标识" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
          <Form.Item name="timezone" label="时区" initialValue="Asia/Shanghai">
            <Select options={TIMEZONES} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export { TIMEZONES }
