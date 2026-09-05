/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe Monaco onMount 共用：绑 SQL 库表列补全。
 */
import { useEffect, useRef } from 'react'
import { bindMonacoSqlSchemaCompletion } from '../utils/monacoSqlCompletion'

export function useSqlSchemaCompletion(opts: {
  datasourceId: number | null | undefined
  defaultCatalog?: string | null
}) {
  const dsRef = useRef(opts.datasourceId)
  const catalogRef = useRef(opts.defaultCatalog)
  dsRef.current = opts.datasourceId
  catalogRef.current = opts.defaultCatalog

  const disposeRef = useRef<(() => void) | null>(null)

  useEffect(() => () => {
    disposeRef.current?.()
    disposeRef.current = null
  }, [])

  const bind = (editor: unknown, monaco: unknown) => {
    disposeRef.current?.()
    disposeRef.current = bindMonacoSqlSchemaCompletion(editor, monaco, {
      getDatasourceId: () => dsRef.current,
      getDefaultCatalog: () => catalogRef.current ?? null,
    })
    const ed = editor as { onDidDispose?: (cb: () => void) => { dispose: () => void } }
    ed.onDidDispose?.(() => {
      disposeRef.current?.()
      disposeRef.current = null
    })
  }

  return { bindSqlSchemaCompletion: bind }
}
