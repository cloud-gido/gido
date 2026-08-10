/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 系统冒烟（构建产物）：生产 bundle 须包含工作台壳标识，
 * 防止三页复用在打包阶段被 tree-shake / 回退弄丢。
 */
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const distDir = resolve(__dirname, '../../dist')

describe('studio workbench system bundle smoke', () => {
  it('production dist contains StudioWorkbench / bleed layout markers', () => {
    let assets: string[]
    try {
      assets = readdirSync(resolve(distDir, 'assets'))
    } catch {
      throw new Error('dist/ 不存在：请先 npm run build 再跑本用例')
    }
    const jsName = assets.find(n => /^index-.*\.js$/.test(n))
    expect(jsName, 'missing hashed index-*.js in dist/assets').toBeTruthy()
    const js = readFileSync(resolve(distDir, 'assets', jsName!), 'utf8')
    expect(js).toMatch(/StudioWorkbench|100vh - 112px/)
  })
})
