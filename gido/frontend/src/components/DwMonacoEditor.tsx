/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 共享 Monaco 壳：DW 主题注册、编辑器外观、可选查找条与注释快捷键。
 * Studio / Probe / Stream / NodeConfigModal / API SQL 模板等复用视觉与基础绑定；
 * 试跑等业务回调由调用方通过 onRun / onMount 注入，避免壳层耦合页面逻辑。
 */
import { useRef, type CSSProperties } from 'react'
import Editor, { type OnMount } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import {
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  registerDwMonacoThemes,
  type EditorAppearance,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from './MonacoFindBar'
import { bindMonacoScriptKeybindings } from '../utils/monacoScriptKeybindings'

export type DwMonacoEditorProps = {
  value?: string
  onChange?: (value: string) => void
  height?: string | number
  language?: string
  readOnly?: boolean
  /** 不传则读本机编辑器外观（与 Studio 等共用 localStorage） */
  appearance?: EditorAppearance
  /** 合并到共享 options 之后 */
  options?: editor.IStandaloneEditorConstructionOptions
  /** 自研查找条 + Cmd/Ctrl+F；默认开启 */
  findBar?: boolean
  /** 提供后绑定 Cmd/Ctrl+Enter 与选区 ▶ */
  onRun?: (script: string, meta: { fromSelection: boolean }) => void
  enableRun?: boolean | (() => boolean)
  onMount?: OnMount
  style?: CSSProperties
  className?: string
  'data-testid'?: string
}

export default function DwMonacoEditor({
  value,
  onChange,
  height = 240,
  language = 'sql',
  readOnly = false,
  appearance: appearanceProp,
  options,
  findBar = true,
  onRun,
  enableRun,
  onMount,
  style,
  className,
  'data-testid': dataTestId,
}: DwMonacoEditorProps) {
  const appearance = appearanceProp ?? loadEditorAppearance()
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const findApiRef = useRef<MonacoFindBarApi | null>(null)

  const handleMount: OnMount = (ed, monaco) => {
    editorRef.current = ed
    if (findBar) {
      bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
    }
    bindMonacoScriptKeybindings(ed, monaco, { onRun, enableRun })
    onMount?.(ed, monaco)
  }

  const fillParent = height === '100%' || style?.height === '100%'

  return (
    <div
      className={className}
      data-testid={dataTestId}
      style={{
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        overflow: 'hidden',
        position: 'relative',
        display: fillParent ? 'flex' : undefined,
        flexDirection: fillParent ? 'column' : undefined,
        minHeight: fillParent ? 0 : undefined,
        ...style,
      }}
    >
      {findBar ? (
        <MonacoFindBar
          getEditor={() => editorRef.current}
          apiRef={findApiRef}
          readOnly={readOnly}
          theme={appearance.theme}
        />
      ) : null}
      <div style={fillParent ? { flex: 1, minHeight: 0 } : undefined}>
        <Editor
          height={height}
          language={language}
          theme={appearance.theme}
          value={value ?? ''}
          onChange={(v) => {
            if (readOnly) return
            onChange?.(v ?? '')
          }}
          beforeMount={registerDwMonacoThemes}
          onMount={handleMount}
          options={{
            ...monacoEditorOptionsFromAppearance(appearance),
            readOnly,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            ...options,
          }}
        />
      </div>
    </div>
  )
}
