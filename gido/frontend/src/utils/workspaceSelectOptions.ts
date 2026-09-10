/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 顶栏空间 Select 选项：保证当前空间始终有 label，避免切换子产品 remount 时空 options 闪出 id。
 */
import { workspaceSwitcherLabel } from './roleLabels'

export type WorkspaceSelectOption = { value: number; label: string }

export function buildWorkspaceSelectOptions(
  workspaces: any[] | null | undefined,
  currentWorkspace?: any | null,
  labelFn: (w: any) => string = workspaceSwitcherLabel,
): WorkspaceSelectOption[] {
  const seen = new Set<number>()
  const opts: WorkspaceSelectOption[] = []
  for (const w of workspaces || []) {
    const id = Number(w?.id)
    if (!Number.isFinite(id) || seen.has(id)) continue
    seen.add(id)
    opts.push({ value: id, label: labelFn(w) })
  }
  const curId = Number(currentWorkspace?.id)
  if (Number.isFinite(curId) && !seen.has(curId)) {
    opts.unshift({ value: curId, label: labelFn(currentWorkspace) })
  }
  return opts
}

/** 选中项展示：优先 options label，否则用当前空间名（永不回退成裸 id） */
export function workspaceSelectDisplayLabel(
  value: unknown,
  options: WorkspaceSelectOption[],
  currentWorkspace?: any | null,
): string {
  const id = Number(value)
  const hit = options.find(o => o.value === id)
  if (hit?.label) return hit.label
  if (currentWorkspace && Number(currentWorkspace.id) === id) {
    return workspaceSwitcherLabel(currentWorkspace)
  }
  return workspaceSwitcherLabel({ name: undefined })
}
