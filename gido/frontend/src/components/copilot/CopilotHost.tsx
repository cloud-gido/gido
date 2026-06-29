/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useState } from 'react'
import { Button, Tooltip } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
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
    copilotApi.status().then(setStatus).catch(() => null)
  }, [open])

  const toggle = () => {
    setOpen(prev => {
      const next = !prev
      localStorage.setItem('gido_copilot_open', next ? '1' : '0')
      return next
    })
  }

  const dotClass = status?.configured ? 'copilot-dot--ok' : 'copilot-dot--warn'
  const tip = status?.configured
    ? `玑渡 Copilot · ${status.model}`
    : (status?.message || '玑渡 Copilot · 待配置 LLM')

  return (
    <>
      <Tooltip title={tip}>
        <Button
          type="text"
          className="copilot-launcher dw-link-quiet"
          icon={<RobotOutlined />}
          onClick={toggle}
        >
          <span className={`copilot-dot ${dotClass}`} aria-hidden />
          玑渡 Copilot
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
