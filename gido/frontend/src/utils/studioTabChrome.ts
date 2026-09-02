/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据开发 Tab 铬态：对齐 IDEA/PyCharm（未打开缓冲 / 加载 / 失败 / 就绪）。
 */
import { isEditorTabContentPending } from './editorSessionStore'

export type StudioTabChromeKind = 'ready' | 'pending' | 'loading' | 'error'

export function resolveStudioTabChrome(opts: {
  script_content?: string | null
  loading?: boolean
  error?: string | null
}): StudioTabChromeKind {
  if (opts.loading) return 'loading'
  if (opts.error) return 'error'
  if (isEditorTabContentPending({ script_content: opts.script_content })) return 'pending'
  return 'ready'
}

export function studioTabTitleColor(kind: StudioTabChromeKind, isActive: boolean): string {
  switch (kind) {
    case 'error':
      return '#d48806'
    case 'loading':
      return isActive ? '#1677ff' : '#8c8c8c'
    case 'pending':
      return isActive ? '#69b1ff' : '#8c8c8c'
    default:
      return isActive ? '#1677ff' : '#666'
  }
}

export function studioTabTitleItalic(kind: StudioTabChromeKind): boolean {
  return kind !== 'ready'
}

/** 会话恢复：保证 prefer 在列表中并作为 active，其余保持原序 */
export function planStudioSessionTabOrder(
  sessionTabIds: number[],
  preferId: number | null,
): { tabIds: number[]; activeId: number | null } {
  if (preferId == null) {
    return {
      tabIds: sessionTabIds,
      activeId: sessionTabIds[0] ?? null,
    }
  }
  const tabIds = sessionTabIds.includes(preferId)
    ? sessionTabIds
    : [preferId, ...sessionTabIds]
  return { tabIds, activeId: preferId }
}
