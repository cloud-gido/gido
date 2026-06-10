/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export function pickDefaultWorkspace(workspaces: any[] | null | undefined, user: any | null | undefined): any | null {
  const list = workspaces || []
  if (!list.length) return null
  const defId = user?.default_workspace_id
  if (defId != null) {
    const hit = list.find((w: any) => w.id === defId)
    if (hit) return hit
  }
  const infras = list.find((w: any) => w.name === 'infras')
  if (infras) return infras
  return list[0]
}
