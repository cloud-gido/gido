/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 控制 Ant Design Table 展开行：隐藏默认 ⊕ 列，由 SoftRowDetailToggle 触发。
 */
import { useCallback, useState, type Key } from 'react'

export function useSoftExpandedRows() {
  const [expandedRowKeys, setExpandedRowKeys] = useState<Key[]>([])

  const isExpanded = useCallback(
    (key: Key) => expandedRowKeys.includes(key),
    [expandedRowKeys],
  )

  const toggle = useCallback((key: Key) => {
    setExpandedRowKeys((keys) => (
      keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]
    ))
  }, [])

  return {
    expandedRowKeys,
    setExpandedRowKeys,
    isExpanded,
    toggle,
    /** 与 Table expandable 合并：showExpandColumn: false */
    expandableControl: {
      expandedRowKeys,
      onExpandedRowsChange: (keys: readonly Key[]) => setExpandedRowKeys([...keys]),
      showExpandColumn: false as const,
      expandIcon: () => null,
    },
  }
}
