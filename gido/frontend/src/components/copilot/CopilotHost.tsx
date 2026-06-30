/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useState } from 'react'
import { Button, Tooltip } from 'antd'
import { CommentOutlined } from '@ant-design/icons'
import { copilotApi, type CopilotStatus } from '../../api/copilot'
import CopilotDrawer from './CopilotDrawer'
import './copilot.css'

type Props = {
  workspaceId?: number
}

export default function CopilotHost({ workspaceId }: Props) {
  const [open, setOpen] = useState(() => localStorage.getItem('gido_copilot_open') === '1')
  const [status, setStatus] = useState<CopilotStatus | null>(null)

  useEffect(() => {
    copilotApi.status(workspaceId).then(setStatus).catch(() => null)
  }, [open, workspaceId])

  const toggle = () => {
    setOpen(prev => {
      const next = !prev
      localStorage.setItem('gido_copilot_open', next ? '1' : '0')
      return next
    })
  }

  const ready = status?.configured
  const tip = ready
    ? `玑渡 Copilot · ${status.model}`
    : (status?.message || '玑渡 Copilot · 待配置模型')

  return (
    <>
      <Tooltip title={tip}>
        <Button
          type="text"
          className={`copilot-launcher dw-link-quiet${ready ? ' copilot-launcher--ready' : ''}`}
          icon={<CommentOutlined className="copilot-launcher__icon" />}
          onClick={toggle}
        >
          Copilot
        </Button>
      </Tooltip>
      <CopilotDrawer
        open={open}
        onClose={() => {
          setOpen(false)
          localStorage.setItem('gido_copilot_open', '0')
        }}
        workspaceId={workspaceId}
      />
    </>
  )
}
