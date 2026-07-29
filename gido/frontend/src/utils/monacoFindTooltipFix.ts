/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Monaco Find 关闭钮 title 为 "Close (Escape)"。旧修复在 capture 阶段 blur，
 * 且用 pointer-events:none + 对 title 的 MutationObserver 与 Monaco 互抢，
 * 会导致：(1) Esc 关不掉查找框 (2) (Escape) 原生 tooltip 残影闪烁。
 *
 * 本版：Esc 显式 closeFindWidget；只在打开/悬停时去掉 title；不再 blur、不用 pointer-events。
 */
import type { editor } from 'monaco-editor'

const CLOSE_ACTION = 'closeFindWidget'

function stripTitlesIn(scope: ParentNode | null | undefined) {
  if (!scope) return
  scope.querySelectorAll('.find-widget [title], .replace-widget [title]').forEach(el => {
    const title = el.getAttribute('title') || ''
    if (!title) return
    if (!el.getAttribute('aria-label')) {
      el.setAttribute(
        'aria-label',
        title.replace(/\s*\([^)]*\)\s*$/, '').trim() || 'Close',
      )
    }
    el.removeAttribute('title')
  })
}

function findWidgetVisible(root: HTMLElement | null): boolean {
  if (!root) return false
  const w = root.querySelector('.find-widget') as HTMLElement | null
  if (!w) return false
  // Monaco 用 visibility / class；visible 时一般不是 hidden
  const style = window.getComputedStyle(w)
  return style.visibility !== 'hidden' && style.display !== 'none' && w.getClientRects().length > 0
}

/** @returns dispose */
export function bindMonacoFindChromeTooltipFix(ed: editor.IStandaloneCodeEditor): () => void {
  const root = ed.getDomNode()

  const strip = () => stripTitlesIn(root)

  // 仅在节点增删时处理（查找框出现），不要 observe title——会与 Monaco 死循环闪烁
  const mo = new MutationObserver(mutations => {
    for (const m of mutations) {
      if (m.type === 'childList' && (m.addedNodes.length || m.removedNodes.length)) {
        strip()
        return
      }
      if (m.type === 'attributes' && m.attributeName === 'class') {
        const t = m.target as HTMLElement
        if (t.classList?.contains('find-widget') || t.classList?.contains('replace-widget')) {
          strip()
          return
        }
      }
    }
  })
  if (root) {
    mo.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    })
  }

  // 悬停关闭钮前先去掉 title，避免原生 tooltip 弹出
  const onMouseOver = (e: Event) => {
    const t = e.target as HTMLElement | null
    if (!t?.closest?.('.find-widget, .replace-widget')) return
    strip()
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return
    if (!findWidgetVisible(root)) return

    // 自己关查找框，避免旧逻辑 blur 抢走 Monaco 的 Esc
    e.preventDefault()
    e.stopPropagation()
    try {
      const action = ed.getAction(CLOSE_ACTION)
      if (action?.isSupported()) {
        void action.run()
      } else {
        ed.trigger('keyboard', CLOSE_ACTION, null)
      }
    } catch {
      try {
        ed.trigger('keyboard', CLOSE_ACTION, null)
      } catch {
        /* ignore */
      }
    }
    strip()
    // 关闭后把焦点还给编辑器，顺带让浏览器丢掉已显示的原生 tooltip
    window.setTimeout(() => {
      strip()
      try {
        ed.focus()
      } catch {
        /* ignore */
      }
    }, 0)
  }

  // 点击关闭钮前去掉 title（捕获阶段）
  const onPointerDown = (e: PointerEvent) => {
    const t = e.target as HTMLElement | null
    if (!t?.closest?.('.find-widget .codicon-widget-close, .find-widget [aria-label], .replace-widget')) return
    strip()
  }

  window.addEventListener('keydown', onKeyDown, true)
  root?.addEventListener('mouseover', onMouseOver, true)
  root?.addEventListener('pointerdown', onPointerDown, true)
  strip()

  const dispose = () => {
    mo.disconnect()
    window.removeEventListener('keydown', onKeyDown, true)
    root?.removeEventListener('mouseover', onMouseOver, true)
    root?.removeEventListener('pointerdown', onPointerDown, true)
  }
  ed.onDidDispose(dispose)
  return dispose
}
