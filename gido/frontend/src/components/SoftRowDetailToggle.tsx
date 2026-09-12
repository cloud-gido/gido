/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 表格行展开的弱提示开关：不用 Ant Design 默认 ⊕，避免与列表割裂。
 */
import { RightOutlined } from '@ant-design/icons'
import './SoftRowDetailToggle.css'

export default function SoftRowDetailToggle({
  expanded,
  onToggle,
  label = '明细',
}: {
  expanded: boolean
  onToggle: () => void
  label?: string
}) {
  return (
    <button
      type="button"
      className={`gido-soft-row-detail${expanded ? ' is-open' : ''}`}
      onClick={(e) => {
        e.stopPropagation()
        onToggle()
      }}
      aria-expanded={expanded}
    >
      <RightOutlined className="gido-soft-row-detail__chevron" aria-hidden />
      <span>{expanded ? '收起' : label}</span>
    </button>
  )
}
