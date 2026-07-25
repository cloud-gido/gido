/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 自动草稿默认静默：成功路径不占工具栏、不改布局。
 * 仅在失败时提示（内容已本地兜底）。
 */
import { Tooltip } from 'antd'
import { ExclamationCircleOutlined } from '@ant-design/icons'
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
  // 静默成功：pending / saving / saved / idle 均不渲染，避免工具栏文字跳动
  if (!visible || status !== 'error') return null

  const title = localAuthority
    ? '自动保存到本机失败，请检查浏览器存储权限'
    : '自动保存失败，内容已缓存在本机；恢复网络后继续编辑即可重试'

  const label = localAuthority
    ? '自动保存失败'
    : (hint ? `自动保存失败（${hint}）` : '自动保存失败（本地已保留）')

  return (
    <Tooltip title={title}>
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          fontSize: 12,
          color: '#cf1322',
          marginLeft: 4,
          userSelect: 'none',
        }}
      >
        <ExclamationCircleOutlined />
        {label}
      </span>
    </Tooltip>
  )
}
