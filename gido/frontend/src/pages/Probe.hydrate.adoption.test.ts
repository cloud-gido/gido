/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：探查进页水合与工作台复用，避免全屏加载闪一下。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('Probe page hydrate adoption', () => {
  it('hydrates from probeLocalStore helpers and does not blank ready tree on every wsId tick', () => {
    const probe = read('pages/Probe.tsx')
    expect(probe).toContain('initialProbeWorkspaceState')
    expect(probe).toContain('probeTreeReadyFromCache')
    expect(probe).toContain('useLayoutEffect')
    // 禁止旧写法：一进页无条件 setTreeReady(false) 再等远端（侧栏已画、主区闪 Spin）
    expect(probe).not.toMatch(/setTreeReady\(false\)\s*\n\s*const local = loadProbeState/)
  })

  it('reuses Studio workbench shell and shared folder tree', () => {
    const probe = read('pages/Probe.tsx')
    expect(probe).toContain('StudioWorkbenchShell')
    expect(probe).toContain('WorkspaceFolderTree')
    expect(probe).toContain('useScriptAutosave')
  })
})
