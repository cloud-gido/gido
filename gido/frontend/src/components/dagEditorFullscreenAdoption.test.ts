/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 集成/回归契约：全屏叠层常量被 DAGEditor / NodeConfigModal 正确采用，
 * 禁止回退到「全屏挂 body 却不抬 zIndex」导致脚本下拉被挡。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  Z_FULLSCREEN,
  Z_NODE_CONFIG,
  Z_NODE_CONFIG_CONFIRM,
  Z_NODE_TIP,
  Z_POPUP,
  assertDagOverlayStackOrder,
} from './dagEditorOverlay'

const root = resolve(__dirname)

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DAG fullscreen overlay adoption', () => {
  it('overlay module exports a valid stack', () => {
    expect(() => assertDagOverlayStackOrder()).not.toThrow()
  })

  it('DAGEditor imports overlay helpers and raises popup in fullscreen', () => {
    const src = read('DAGEditor.tsx')
    expect(src).toContain("from './dagEditorOverlay'")
    expect(src).toContain('dagPopupZIndex')
    expect(src).toContain('dagPopupBase')
    expect(src).toContain('filterPublishedScriptOption')
    expect(src).toContain('Z_FULLSCREEN')
    expect(src).toContain('Z_NODE_TIP')
    expect(src).toContain('getPopupContainer={popupContainer}')
    expect(src).toContain('zIndexPopupBase')
    // 回归：禁止再写「全屏时挂 body 且不抬 zIndex」的旧逻辑
    expect(src).not.toMatch(/fullscreen\s*\?\s*document\.body\s*:\s*wrapRef/)
  })

  it('DAGEditor only offers published scripts in add-node Select', () => {
    const src = read('DAGEditor.tsx')
    expect(src).toContain('is_published')
    expect(src).toContain('publishedNodes')
    expect(src).toContain('只能添加已提交的脚本')
  })

  it('NodeConfigModal uses shared overlay z-index above fullscreen shell', () => {
    const src = read('NodeConfigModal.tsx')
    expect(src).toContain("from './dagEditorOverlay'")
    expect(src).toContain('Z_NODE_CONFIG')
    expect(src).toContain('Z_NODE_CONFIG_CONFIRM')
    expect(src).toContain('zIndex={Z_NODE_CONFIG}')
    expect(src).toContain('zIndex: Z_NODE_CONFIG_CONFIRM')
    expect(Z_NODE_CONFIG).toBeGreaterThan(Z_FULLSCREEN)
    expect(Z_NODE_CONFIG_CONFIRM).toBeGreaterThan(Z_NODE_CONFIG)
    expect(Z_POPUP).toBeGreaterThan(Z_FULLSCREEN)
    expect(Z_NODE_TIP).toBeGreaterThanOrEqual(Z_POPUP)
  })

  it('Workflow page wires DAGEditor and NodeConfigModal together', () => {
    const src = read('../pages/Workflow.tsx')
    expect(src).toContain("from '../components/DAGEditor'")
    expect(src).toContain("from '../components/NodeConfigModal'")
    expect(src).toContain('<DAGEditor')
    expect(src).toContain('<NodeConfigModal')
    expect(src).toContain('onNodeDoubleClick')
  })
})
