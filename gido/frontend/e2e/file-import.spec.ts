/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 文件导入生产基线 E2E：打开 Drawer、Tab 门禁、schema 步骤与写入模式可见。
 * API 全部 mock；依赖 preview build（与 studio-session 相同）。
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
    'gido:batch:integration:read',
    'gido:batch:integration:write',
    'gido:batch:integration:run',
    'gido:batch:datasource:read',
  ],
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installApiMocks(page: Page) {
  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const url = req.url()
    const method = req.method()

    if (url.includes('/auth/me') || url.includes('/auth/profile')) {
      return json(route, USER)
    }
    if (url.includes('/workspaces') && method === 'GET') {
      return json(route, [WS])
    }
    if (url.includes('/datasources') && method === 'GET') {
      return json(route, [
        {
          id: 10,
          name: 'doris_demo',
          ds_type: 'doris',
          workspace_id: 1,
          is_active: true,
        },
      ])
    }
    if (url.includes('/integration/tasks') && method === 'GET') {
      return json(route, [])
    }
    if (url.includes('/file-import/preview-ddl')) {
      return json(route, {
        ddl: 'CREATE TABLE import_demo (id BIGINT)',
        table_exists: false,
      })
    }
    if (url.includes('/file-import/schema-diff')) {
      return json(route, {
        table_exists: false,
        diff: { compatible: true, missing_in_target: [], type_mismatch: [] },
      })
    }
    if (url.includes('/file-import/upload') && method === 'POST') {
      return json(route, {
        file_id: 'e2e-file-1',
        format: 'csv',
        original_filename: 'demo.csv',
        columns: [
          { name: 'id', type: 'bigint', nullable: true, is_primary_key: false },
          { name: 'name', type: 'string', nullable: true, is_primary_key: false },
        ],
        preview_rows: [['1', 'a']],
        row_count: 1,
        encoding: 'utf-8',
        delimiter: ',',
        has_header: true,
      })
    }
    if (method === 'GET') return json(route, [])
    return json(route, { ok: true })
  })
}

test.describe('File import production E2E', () => {
  test('opens drawer from action query and enforces tab gate', async ({ page }) => {
    await installApiMocks(page)

    await page.addInitScript(({ user, ws }) => {
      localStorage.setItem('token', 'e2e-token')
      localStorage.setItem('user', JSON.stringify(user))
      localStorage.setItem('workspace', JSON.stringify(ws))
    }, { user: USER, ws: WS })

    await page.goto('/gido/batch/integration?action=file-import')

    const drawer = page.getByRole('dialog').filter({ hasText: '本地文件导入' })
    await expect(drawer).toBeVisible({ timeout: 30_000 })
    await expect(drawer.getByText('选择文件')).toBeVisible()
    await expect(drawer.getByText('字段与建表')).toBeVisible()
    await expect(drawer.getByText('数据去向')).toBeVisible()

    // 未上传时点「数据去向」应被门禁拦回
    await drawer.getByText('数据去向').click()
    await expect(page.getByText('请先完成文件上传')).toBeVisible({ timeout: 5_000 })
  })

  test('toolbar button opens drawer with operation modes', async ({ page }) => {
    await installApiMocks(page)

    await page.addInitScript(({ user, ws }) => {
      localStorage.setItem('token', 'e2e-token')
      localStorage.setItem('user', JSON.stringify(user))
      localStorage.setItem('workspace', JSON.stringify(ws))
    }, { user: USER, ws: WS })

    await page.goto('/gido/batch/integration')
    await expect(page.getByRole('heading', { name: '数据集成' })).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: '本地文件导入' }).click()

    const drawer = page.getByRole('dialog').filter({ hasText: '本地文件导入' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByText('断点续传')).toBeVisible()
  })
})
