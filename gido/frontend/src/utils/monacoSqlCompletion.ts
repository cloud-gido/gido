/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Monaco SQL 库/表/列补全：触发解析 + CompletionItemProvider。
 */
import { fetchColumns, fetchSchemas, fetchTables } from './sqlSchemaCache'

const SQL_KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS',
  'ON', 'AND', 'OR', 'NOT', 'IN', 'EXISTS', 'BETWEEN', 'LIKE', 'IS', 'NULL', 'AS',
  'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL', 'DISTINCT',
  'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE', 'TABLE', 'VIEW',
  'DROP', 'ALTER', 'WITH', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'OVERWRITE', 'PARTITION',
  'USE', 'SHOW', 'DESCRIBE', 'EXPLAIN', 'TRUNCATE', 'REPLACE', 'IF', 'TRUE', 'FALSE',
]

export type SqlCompletionContext =
  | { kind: 'bare'; prefix: string }
  | { kind: 'after_dot'; left: string; prefix: string }
  | { kind: 'column_qualified'; catalog: string; table: string; prefix: string }

/** 取光标前一行（或末尾）用于解析补全触发。 */
export function textBeforeCursorOnLine(lineContent: string, column: number): string {
  return lineContent.slice(0, Math.max(0, column - 1))
}

/**
 * 解析 `db.table.` / `foo.` / 裸前缀。
 * left 可能是 catalog、表名或别名，由调用方结合 schemas 集合判定。
 */
export function parseSqlCompletionContext(textBefore: string): SqlCompletionContext {
  const qual = textBefore.match(/([A-Za-z0-9_$#`]+)\.([A-Za-z0-9_$#`]+)\.([A-Za-z0-9_$#`]*)$/)
  if (qual) {
    return {
      kind: 'column_qualified',
      catalog: stripTicks(qual[1]),
      table: stripTicks(qual[2]),
      prefix: stripTicks(qual[3]),
    }
  }
  const m = textBefore.match(/([A-Za-z0-9_$#`]+)\.([A-Za-z0-9_$#`]*)$/)
  if (m) {
    return { kind: 'after_dot', left: stripTicks(m[1]), prefix: stripTicks(m[2]) }
  }
  const bare = textBefore.match(/([A-Za-z0-9_$#`]*)$/)
  return { kind: 'bare', prefix: stripTicks(bare?.[1] || '') }
}

function stripTicks(s: string): string {
  return s.replace(/`/g, '')
}

function filterByPrefix<T extends { name: string }>(items: T[], prefix: string): T[] {
  const p = prefix.toLowerCase()
  if (!p) return items
  return items.filter(i => i.name.toLowerCase().startsWith(p) || i.name.toLowerCase().includes(p))
}

export type BindSqlCompletionOpts = {
  getDatasourceId: () => number | null | undefined
  /** 数据源默认库，用于表插入写成 catalog.table */
  getDefaultCatalog?: () => string | null | undefined
}

/**
 * 注册 sql 语言补全；返回 dispose。无数据源时 provider 仍注册但返回空建议。
 */
export function bindMonacoSqlSchemaCompletion(
  _editor: unknown,
  monaco: any,
  opts: BindSqlCompletionOpts,
): () => void {
  const disposable = monaco.languages.registerCompletionItemProvider('sql', {
    triggerCharacters: ['.', ' ', '`'],
    provideCompletionItems: async (model: any, position: any) => {
      const dsId = opts.getDatasourceId()
      const line = model.getLineContent(position.lineNumber) as string
      const before = textBeforeCursorOnLine(line, position.column)
      const ctx = parseSqlCompletionContext(before)
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      // 点后替换「点后面的前缀」；裸词替换当前 word
      const prefixLen = ctx.kind === 'bare' ? ctx.prefix.length : ctx.prefix.length
      const dotRange = ctx.kind !== 'bare'
        ? {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: position.column - prefixLen,
            endColumn: position.column,
          }
        : range

      const suggestions: any[] = []
      const Kind = monaco.languages.CompletionItemKind

      if (dsId == null || !Number.isFinite(dsId)) {
        return { suggestions }
      }

      const defaultCatalog = (opts.getDefaultCatalog?.() || '').trim() || null

      try {
        if (ctx.kind === 'column_qualified') {
          const columns = filterByPrefix(
            await fetchColumns(dsId, ctx.table, ctx.catalog),
            ctx.prefix,
          )
          for (const c of columns) {
            suggestions.push({
              label: c.name,
              kind: Kind.Field,
              detail: c.type || 'column',
              insertText: c.name,
              range: dotRange,
              sortText: `0_${c.name}`,
            })
          }
        } else if (ctx.kind === 'after_dot') {
          const schemas = await fetchSchemas(dsId)
          const schemaNames = new Set(schemas.map(s => s.name.toLowerCase()))
          if (schemaNames.has(ctx.left.toLowerCase())) {
            const tables = filterByPrefix(await fetchTables(dsId, ctx.left), ctx.prefix)
            for (const t of tables) {
              suggestions.push({
                label: t.name,
                kind: Kind.Class,
                detail: t.comment || t.type || 'table',
                insertText: t.name,
                range: dotRange,
                sortText: `1_${t.name}`,
              })
            }
          } else {
            // 当作 table / alias → 列（默认库）
            const columns = filterByPrefix(
              await fetchColumns(dsId, ctx.left, defaultCatalog),
              ctx.prefix,
            )
            for (const c of columns) {
              suggestions.push({
                label: c.name,
                kind: Kind.Field,
                detail: c.type || 'column',
                insertText: c.name,
                range: dotRange,
                sortText: `0_${c.name}`,
              })
            }
          }
        } else {
          const prefix = ctx.prefix
          // schemas
          const schemas = filterByPrefix(await fetchSchemas(dsId), prefix)
          for (const s of schemas) {
            suggestions.push({
              label: s.name,
              kind: Kind.Module,
              detail: s.is_default ? 'default catalog' : 'catalog',
              insertText: s.name,
              range,
              sortText: `2_${s.name}`,
            })
          }
          // default catalog tables as catalog.table
          const tables = filterByPrefix(await fetchTables(dsId, defaultCatalog), prefix)
          for (const t of tables) {
            const cat = (t.catalog || defaultCatalog || '').trim()
            const insert = cat ? `${cat}.${t.name}` : t.name
            suggestions.push({
              label: insert,
              kind: Kind.Class,
              detail: t.comment || t.type || 'table',
              insertText: insert,
              range,
              sortText: `1_${t.name}`,
            })
          }
          // keywords
          for (const kw of SQL_KEYWORDS) {
            if (prefix && !kw.toLowerCase().startsWith(prefix.toLowerCase())) continue
            suggestions.push({
              label: kw,
              kind: Kind.Keyword,
              insertText: kw,
              range,
              sortText: `3_${kw}`,
            })
          }
        }
      } catch {
        // 补全失败静默：不打断编辑
      }

      return { suggestions }
    },
  })

  return () => {
    try {
      disposable?.dispose?.()
    } catch {
      /* ignore */
    }
  }
}
