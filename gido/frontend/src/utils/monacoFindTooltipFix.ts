/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Monaco Find/Replace 关闭按钮 title 形如 "Close (Escape)"。
 * Esc 或点击关闭后，Chromium 原生 tooltip 常残影闪烁「(Escape)」。
 * 持续去掉 title（保留 aria-label），Esc 时 blur 并短暂禁 pointer-events。
 */
import type { editor } from 'monaco-editor'

const TITLE_SEL = '.find-widget [title], .replace-widget [title], .find-widget [aria-label*="Escape"], .replace-widget [aria-label*="Escape"]'

function stripFindTitles(scope?: ParentNode | null) {
  const root = scope || document
  root.querySelectorAll(TITLE_SEL).forEach(el => {
    const title = el.getAttribute('title') || ''
    const aria = el.getAttribute('aria-label') || ''
    if (title) {
      if (!aria) {
        el.setAttribute('aria-label', title.replace(/\s*\([^)]*\)\s*$/, '').trim() || title)
      }
      el.removeAttribute('title')
    }
    // 部分版本把 (Escape) 写在 aria-label 里，原生也会拿来当提示
    if (aria && /\(\s*Escape\s*\)/i.test(aria)) {
      el.setAttribute('aria-label', aria.replace(/\s*\(\s*Escape\s*\)\s*/gi, ' ').trim() || 'Close')
    }
  })
}

function pulsePointerEventsOff() {
  document.querySelectorAll('.monaco-editor .find-widget, .monaco-editor .replace-widget').forEach(node => {
    const el = node as HTMLElement
    el.style.pointerEvents = 'none'
  })
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      document.querySelectorAll('.monaco-editor .find-widget, .monaco-editor .replace-widget').forEach(node => {
        ;(node as HTMLElement).style.pointerEvents = ''
      })
    })
  })
}

/** @returns dispose */
export function bindMonacoFindChromeTooltipFix(ed: editor.IStandaloneCodeEditor): () => void {
  const root = ed.getDomNode()
  const strip = () => stripFindTitles(root || document)

  const mo = new MutationObserver(() => strip())
  if (root) {
    mo.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['title', 'class', 'aria-label'],
    })
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return
    strip()
    pulsePointerEventsOff()
    const ae = document.activeElement as HTMLElement | null
    if (ae?.closest?.('.find-widget, .replace-widget')) {
      ae.blur()
    }
    // 再扫一遍：关闭动画期间 Monaco 可能重新写入 title
    window.setTimeout(strip, 0)
    window.setTimeout(strip, 50)
    window.setTimeout(strip, 200)
  }

  const onPointerDown = (e: PointerEvent) => {
    const t = e.target as HTMLElement | null
    if (!t?.closest?.('.find-widget, .replace-widget')) return
    strip()
  }

  window.addEventListener('keydown', onKeyDown, true)
  root?.addEventListener('pointerdown', onPointerDown, true)
  strip()

  const dispose = () => {
    mo.disconnect()
    window.removeEventListener('keydown', onKeyDown, true)
    root?.removeEventListener('pointerdown', onPointerDown, true)
  }
  ed.onDidDispose(dispose)
  return dispose
}
