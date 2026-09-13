/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 编辑器使用仓库内 monaco-editor，禁止运行时打 jsDelivr/CDN。
 * Studio / Probe / Stream / NodeConfigModal / DwMonacoEditor 在加载编辑器前 import 本模块。
 */
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.main'
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution'
import 'monaco-editor/esm/vs/language/json/monaco.contribution'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

type MonacoEnv = { getWorker: (workerId: string, label: string) => Worker }

let configured = false

export function setupMonacoLocal(): void {
  if (configured) return
  configured = true
  if (typeof window !== 'undefined') {
    const g = globalThis as typeof globalThis & { MonacoEnvironment?: MonacoEnv }
    g.MonacoEnvironment = {
      getWorker(_workerId: string, label: string) {
        if (label === 'json') return new JsonWorker()
        if (label === 'css' || label === 'scss' || label === 'less') return new CssWorker()
        if (label === 'html' || label === 'handlebars' || label === 'razor') return new HtmlWorker()
        if (label === 'typescript' || label === 'javascript') return new TsWorker()
        return new EditorWorker()
      },
    }
  }
  loader.config({ monaco })
}

setupMonacoLocal()
