/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 浏览器 E2E：会话多 Tab 一次挂壳，仅 active 拉正文；后台 Tab 点击后再拉。
 * 依赖 `npm run build` 后由 playwright webServer 起 preview，API 全部 route mock。
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
    'gido:batch:studio:run',
  ],
}

const NODES = [
  { id: 10, name: 'ads_active', node_type: 'SQL', folder_id: null, script_content: null, is_locked: false, is_published: false },
  { id: 20, name: 'dws_pending', node_type: 'SQL', folder_id: null, script_content: null, is_locked: false, is_published: false },
  { id: 30, name: 'dim_pending', node_type: 'SQL', folder_id: null, script_content: null, is_locked: false, is_published: false },
]

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installApiMocks(page: Page, opts?: { failOnceNodeId?: number }) {
  const failCounts = new Map<number, number>()
  if (opts?.failOnceNodeId != null) {
    failCounts.set(opts.failOnceNodeId, 1)
  }
  const getNodeHits: number[] = []

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
    if (path.includes('/studio/nodes') && req.method() === 'GET' && !/\/nodes\/\d+\/?$/.test(path)) {
      return json(route, NODES)
    }
    if (path.includes('/studio/folders') && req.method() === 'GET') {
      return json(route, [])
    }
    if (path.includes('/datasources') && req.method() === 'GET') {
      return json(route, [])
    }
    if (path.includes('/approvals/pending-count')) {
      return json(route, { count: 0, can_review: true })
    }
    const nodeMatch = path.match(/\/studio\/nodes\/(\d+)\/?$/)
    if (nodeMatch && req.method() === 'GET') {
      const id = Number(nodeMatch[1])
      getNodeHits.push(id)
      const left = failCounts.get(id) ?? 0
      if (left > 0) {
        failCounts.set(id, left - 1)
        return json(route, { detail: '模拟加载失败' }, 500)
      }
      const base = NODES.find(n => n.id === id) || { id, name: `n${id}`, node_type: 'SQL' }
      return json(route, {
        ...base,
        script_content: `-- loaded ${id}\nSELECT ${id}`,
        params: {},
      })
    }
    // 其它 API 默认空成功，避免壳层请求卡死
    if (req.method() === 'GET') return json(route, [])
    return json(route, { ok: true })
  })

  return { getNodeHits }
}

test.describe('Studio editor session E2E', () => {
  test('restores all tab shells together and lazy-loads content', async ({ page }) => {
    const { getNodeHits } = await installApiMocks(page)

    await page.addInitScript(({ user, ws }) => {
      localStorage.setItem('token', 'e2e-token')
      localStorage.setItem('user', JSON.stringify(user))
      localStorage.setItem('workspace', JSON.stringify(ws))
      localStorage.setItem(
        'gido.editorSession.v1.studio.1',
        JSON.stringify({ tabIds: [10, 20, 30], activeId: 10, updatedAt: Date.now() }),
      )
    }, { user: USER, ws: WS })

    await page.goto('/gido/batch/studio')

    await expect(page.getByTestId('studio-tab-10')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('studio-tab-20')).toBeVisible()
    await expect(page.getByTestId('studio-tab-30')).toBeVisible()

    // 后台 Tab 仍为 pending（斜体壳）
    await expect(page.getByTestId('studio-tab-20')).toHaveAttribute('data-chrome', 'pending')
    await expect(page.getByTestId('studio-tab-30')).toHaveAttribute('data-chrome', 'pending')

    // active 已 hydrate
    await expect(page.getByTestId('studio-tab-10')).toHaveAttribute('data-chrome', 'ready', { timeout: 15_000 })
    expect(getNodeHits.filter(id => id === 10).length).toBeGreaterThanOrEqual(1)
    expect(getNodeHits.includes(20)).toBe(false)
    expect(getNodeHits.includes(30)).toBe(false)

    await page.getByTestId('studio-tab-20').click()
    await expect(page.getByTestId('studio-tab-20')).toHaveAttribute('data-chrome', 'ready', { timeout: 15_000 })
    expect(getNodeHits.includes(20)).toBe(true)
  })

  test('failed tab can retry', async ({ page }) => {
    await installApiMocks(page, { failOnceNodeId: 20 })

    await page.addInitScript(({ user, ws }) => {
      localStorage.setItem('token', 'e2e-token')
      localStorage.setItem('user', JSON.stringify(user))
      localStorage.setItem('workspace', JSON.stringify(ws))
      localStorage.setItem(
        'gido.editorSession.v1.studio.1',
        JSON.stringify({ tabIds: [10, 20], activeId: 10, updatedAt: Date.now() }),
      )
    }, { user: USER, ws: WS })

    await page.goto('/gido/batch/studio')
    await expect(page.getByTestId('studio-tab-10')).toHaveAttribute('data-chrome', 'ready', { timeout: 30_000 })

    await page.getByTestId('studio-tab-20').click()
    await expect(page.getByTestId('studio-tab-20')).toHaveAttribute('data-chrome', 'error', { timeout: 15_000 })
    await expect(page.getByTestId('studio-tab-content-retry')).toBeVisible()

    // 产品主路径：再点同一 Tab 强制重试（与编辑区「重新加载」等价）
    await page.getByTestId('studio-tab-20').click()
    await expect(page.getByTestId('studio-tab-20')).toHaveAttribute('data-chrome', 'ready', { timeout: 15_000 })
  })
})
