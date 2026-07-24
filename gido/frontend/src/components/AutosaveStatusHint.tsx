/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Tooltip } from 'antd'
import {
  CloudSyncOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import type { ScriptAutosaveStatus } from '../hooks/useScriptAutosave'

type Props = {
  visible?: boolean
  status: ScriptAutosaveStatus
  hint?: string
  /** local-first（探查）时文案略有不同 */
  localAuthority?: boolean
}

export default function AutosaveStatusHint({
  visible = true,
  status,
  hint = '',
  localAuthority = false,
}: Props) {
  if (!visible || status === 'idle') return null

  const title = status === 'error'
    ? (localAuthority
      ? '自动保存到本机失败，请检查浏览器存储权限'
      : '自动保存失败，内容已缓存在本机；恢复网络后继续编辑即可重试')
    : (localAuthority
      ? '编辑后自动保存到本机，刷新页面不丢稿'
      : '编辑后约 1.5s 自动同步到服务端（不产生版本历史），刷新页面不丢稿')

  const label =
    status === 'pending' ? '待自动保存'
      : status === 'saving' ? '正在自动保存…'
        : status === 'saved'
          ? (localAuthority
            ? `已自动保存到本机${hint ? ` ${hint}` : ''}`
            : `已自动保存${hint ? ` ${hint}` : ''}`)
          : status === 'error'
            ? (localAuthority ? '自动保存失败' : '自动保存失败（本地已保留）')
            : null

  if (!label) return null

  return (
    <Tooltip title={title}>
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 12,
        color: status === 'error' ? '#cf1322' : '#8c8c8c',
        marginLeft: 4,
        userSelect: 'none',
      }}
      >
        {status === 'saving' && <LoadingOutlined />}
        {status === 'saved' && <CloudSyncOutlined style={{ color: '#52c41a' }} />}
        {status === 'pending' && <CloudSyncOutlined />}
        {status === 'error' && <ExclamationCircleOutlined />}
        {label}
      </span>
    </Tooltip>
  )
}
