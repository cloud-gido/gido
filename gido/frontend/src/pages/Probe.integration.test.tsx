/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 集成：有本地探查树时进页不得全屏「加载探查目录…」；远端可慢/挂起。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  const map = new Map<string, string>()
  const storage = {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => { map.set(k, String(v)) },
    removeItem: (k: string) => { map.delete(k) },
    clear: () => { map.clear() },
    get length() { return map.size },
    key: (i: number) => [...map.keys()][i] ?? null,
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
})

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ProbePage from './Probe'
import { useAppStore } from '../store'
import { probeApi, datasourceApi } from '../api'
import { probeStorageKey, type ProbeWorkspaceState } from '../utils/probeLocalStore'

vi.mock('@monaco-editor/react', () => ({
  default: ({ value }: { value?: string }) => (
    <div data-testid="monaco-probe">{value ?? ''}</div>
  ),
}))

vi.mock('../api', () => ({
  probeApi: {
    getTree: vi.fn(),
    saveTree: vi.fn(),
  },
  datasourceApi: {
    list: vi.fn(),
  },
}))

const getTree = vi.mocked(probeApi.getTree)
const saveTree = vi.mocked(probeApi.saveTree)
const listDs = vi.mocked(datasourceApi.list)

const WS = { id: 7, name: 'infras', my_role: 'admin', timezone: 'Asia/Shanghai' }

const cached: ProbeWorkspaceState = {
  folders: [],
  scripts: [{
    id: 's-cache',
    name: '缓存查询',
    folderId: null,
    sql: 'SELECT 99 AS x',
    limit: 10000,
  }],
  activeScriptId: 's-cache',
}

function renderProbe() {
  return render(
    <MemoryRouter>
      <ProbePage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  useAppStore.setState({
    user: {
      id: 1,
      username: 'admin',
      is_admin: true,
      permissions: ['gido:batch:probe:read', 'gido:batch:probe:write'],
    },
    currentWorkspace: WS,
    workspaces: [WS],
  })
  listDs.mockResolvedValue([] as any)
  saveTree.mockResolvedValue({ ok: true } as any)
})

afterEach(() => {
  cleanup()
  getTree.mockReset()
  saveTree.mockReset()
  listDs.mockReset()
})

describe('Probe hydrate integration', () => {
  it('with local cache does not show full-screen catalog loading even if remote hangs', async () => {
    localStorage.setItem(probeStorageKey(WS.id), JSON.stringify(cached))
    getTree.mockImplementation(() => new Promise(() => {}))

    renderProbe()

    expect(screen.queryByText(/加载探查目录与脚本/)).not.toBeInTheDocument()
    expect(await screen.findByTestId('probe-active-script-title')).toHaveTextContent('缓存查询')
    expect(screen.getByTestId('monaco-probe')).toHaveTextContent('SELECT 99 AS x')
  })

  it('cold start waits for remote then shows editor without sticky loading', async () => {
    getTree.mockResolvedValue({
      folders: [],
      scripts: [{
        id: 's-remote',
        name: '远端查询',
        folderId: null,
        sql: 'SELECT 1',
        limit: 10000,
      }],
      activeScriptId: 's-remote',
    } as any)

    renderProbe()

    await waitFor(() => {
      expect(screen.queryByText(/加载探查目录与脚本/)).not.toBeInTheDocument()
    })
    expect(await screen.findByTestId('probe-active-script-title')).toHaveTextContent('远端查询')
  })
})
