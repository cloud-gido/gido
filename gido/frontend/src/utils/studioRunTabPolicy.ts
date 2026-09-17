/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

export type StudioRunTab = 'log' | 'result'

export function nextStudioRunTab(input: {
  current: StudioRunTab
  status: string
  nodeType?: string
  result?: unknown
  manuallySelected: boolean
}): StudioRunTab {
  if (['failed', 'timed_out', 'cancelled'].includes(input.status)) return 'log'
  if (input.status !== 'success' || input.nodeType !== 'SQL' || input.manuallySelected) {
    return input.current
  }
  const columns = (input.result as { columns?: unknown } | null)?.columns
  return Array.isArray(columns) && columns.length > 0 ? 'result' : input.current
}
