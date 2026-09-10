/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * E2E：统一工作台壳切批/流/服不换空间；探查有本地缓存时不闪全屏加载。
 * 依赖 `npm run build` + playwright webServer preview；API 全 mock。
 */
import { test, expect, type Page, type Route } from '@playwright/test'

const WS = {
  id: 1,
  name: 'e2e-ws',
  my_role: 'admin',
  timezone: 'Asia/Shanghai',
  owner_id: 1,
}

const USER = {
  id: 1,
  username: 'admin',
  full_name: 'Admin',
  is_admin: true,
  is_active: true,
  permissions: [
    'gido:batch:studio:read',
    'gido:batch:studio:write',
    'gido:batch:probe:read',
    'gido:batch:probe:write',
    'gido:stream:read',
    'gido:stream:write',
    'gido:service:read',
    'gido:service:write',
  ],
}

const PROBE_LOCAL = {
  folders: [],
  scripts: [{
    id: 's-local',
    name: '本地探查',
    folderId: null,
    sql: 'SELECT 7',
    limit: 10000,
  }],
  activeScriptId: 's-local',
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installApiMocks(page: Page, opts?: { probeTreeDelayMs?: number }) {
  const delay = opts?.probeTreeDelayMs ?? 0

  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace(/^\/api/, '') || url.pathname

    if (path === '/auth/me' || path.endsWith('/auth/me')) {
      return json(route, USER)
    }
    if (path === '/workspaces' || path.endsWith('/workspaces')) {
      return json(route, [WS])
    }
    if (path.match(/\/workspaces\/\d+$/) && req.method() === 'GET') {
      return json(route, WS)
    }
    if (path.includes('/probe/tree') && req.method() === 'GET') {
      if (delay > 0) await new Promise(r => setTimeout(r, delay))
      return json(route, {
        folders: [],
        scripts: [{
          id: 's-remote',
          name: '远端探查',
          folderId: null,
          sql: 'SELECT 1',
          limit: 10000,
        }],
        activeScriptId: 's-remote',
      })
    }
    if (path.includes('/probe/tree') && req.method() === 'PUT') {
      return json(route, { ok: true })
    }
    if (path.includes('/studio/nodes') && req.method() === 'GET') {
      return json(route, [])
    }
    if (path.includes('/studio/folders') && req.method() === 'GET') {
      return json(route, [])
    }
    if (path.includes('/datasources') && req.method() === 'GET') {
      return json(route, [])
    }
    if (path.includes('/streaming/') || path.includes('/stream/')) {
      if (req.method() === 'GET') return json(route, [])
      return json(route, { ok: true })
    }
    if (path.includes('/data-service') || path.includes('/dataservice')) {
      if (req.method() === 'GET') return json(route, Array.isArray(path) ? [] : [])
      return json(route, { ok: true })
    }
    if (path.includes('/approvals/pending-count')) {
      return json(route, { count: 0, can_review: true })
    }
    if (req.method() === 'GET') return json(route, [])
    return json(route, { ok: true })
  })
}

async function seedAuth(page: Page, extra?: Record<string, string>) {
  await page.addInitScript(({ user, ws, extraStorage }) => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('user', JSON.stringify(user))
    localStorage.setItem('workspace', JSON.stringify(ws))
    for (const [k, v] of Object.entries(extraStorage || {})) {
      localStorage.setItem(k, v)
    }
  }, { user: USER, ws: WS, extraStorage: extra ?? {} })
}

function workspaceSelect(page: Page) {
  return page.locator('.dw-header-workspace-select')
}

test.describe('Workspace product shell E2E', () => {
  test('switching batch/stream/service keeps workspace label', async ({ page }) => {
    await installApiMocks(page)
    await seedAuth(page)

    await page.goto('/gido/batch/studio')
    await expect(workspaceSelect(page)).toContainText('e2e-ws', { timeout: 30_000 })

    await page.getByRole('button', { name: '切换 GIDO 子产品' }).click()
    await page.locator('.dw-product-launcher-card--stream').click()
    await expect(page).toHaveURL(/\/gido\/stream/, { timeout: 15_000 })
    await expect(workspaceSelect(page)).toContainText('e2e-ws')

    await page.getByRole('button', { name: '切换 GIDO 子产品' }).click()
    await page.locator('.dw-product-launcher-card--service').click()
    await expect(page).toHaveURL(/\/gido\/service/, { timeout: 15_000 })
    await expect(workspaceSelect(page)).toContainText('e2e-ws')

    await page.getByRole('button', { name: '切换 GIDO 子产品' }).click()
    await page.locator('.dw-product-launcher-card--batch').click()
    await expect(page).toHaveURL(/\/gido\/batch/, { timeout: 15_000 })
    await expect(workspaceSelect(page)).toContainText('e2e-ws')
  })

  test('probe with local cache does not flash full-screen catalog loading', async ({ page }) => {
    await installApiMocks(page, { probeTreeDelayMs: 2500 })
    await seedAuth(page, {
      'gido.probe.tree.v1.w1': JSON.stringify(PROBE_LOCAL),
    })

    await page.goto('/gido/batch/probe')

    // 有本地缓存：在远端延迟期间就应看到编辑态，且不得出现全屏加载文案
    await expect(page.getByTestId('probe-active-script-title')).toHaveText('本地探查', { timeout: 5_000 })
    await expect(page.getByText('加载探查目录与脚本')).toHaveCount(0)

    // 再进一次仍不闪
    await page.goto('/gido/batch/studio')
    await expect(workspaceSelect(page)).toContainText('e2e-ws', { timeout: 15_000 })
    await page.goto('/gido/batch/probe')
    await expect(page.getByTestId('probe-active-script-title')).toHaveText('本地探查', { timeout: 5_000 })
    await expect(page.getByText('加载探查目录与脚本')).toHaveCount(0)
  })
})
