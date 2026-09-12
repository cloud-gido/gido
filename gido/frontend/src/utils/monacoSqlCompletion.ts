/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Monaco SQL 库/表/列补全（业界常见心智的轻量实现）：
 * - 子句槽位：FROM/JOIN 推表；SELECT/WHERE/… 推列
 * - 作用域：最内层括号子查询优先，外层表可用于关联子查询
 * - CTE：WITH 名可作表；投影列（简单 SELECT 列表）可作列
 * - 解析前剥离注释与字符串，减少误匹配
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

const TABLE_SLOT_KEYWORDS = new Set([
  'from', 'join', 'into', 'update', 'table', 'use',
])

const COLUMN_SLOT_KEYWORDS = new Set([
  'select', 'where', 'on', 'having', 'set', 'by', 'and', 'or', 'when', 'then', 'else',
  'between', 'like', 'in', 'not', 'is',
])

const NOT_TABLE_ALIAS = new Set([
  'where', 'join', 'left', 'right', 'inner', 'outer', 'full', 'cross', 'on', 'group',
  'order', 'limit', 'having', 'union', 'set', 'as', 'and', 'or', 'using', 'into',
  'values', 'select', 'when', 'then', 'else', 'end', 'by', 'asc', 'desc', 'with',
  'lateral', 'natural', 'partition', 'over', 'window', 'recursive', 'materialized',
])

export type SqlCompletionContext =
  | { kind: 'bare'; prefix: string }
  | { kind: 'after_dot'; left: string; prefix: string }
  | { kind: 'column_qualified'; catalog: string; table: string; prefix: string }

export type SqlSuggestSlot = 'table_slot' | 'column_slot' | 'keyword_only'

export type SqlTableRef = {
  catalog: string | null
  table: string
  alias: string | null
  kind?: 'physical' | 'cte' | 'subquery'
  /** CTE / 子查询已解析出的投影列名 */
  columns?: string[]
}

export type SqlCteDef = {
  name: string
  body: string
  columns: string[]
}

/** 取光标前一行（或末尾）用于解析补全触发。 */
export function textBeforeCursorOnLine(lineContent: string, column: number): string {
  return lineContent.slice(0, Math.max(0, column - 1))
}

/**
 * 剥离注释与字符串字面量，保留长度占位，避免偏移错位。
 * 供结构解析使用；补全触发仍用原文。
 */
export function stripSqlNoise(sql: string): string {
  let out = ''
  let i = 0
  while (i < sql.length) {
    const c = sql[i]
    const n = sql[i + 1]
    if (c === '-' && n === '-') {
      out += '  '
      i += 2
      while (i < sql.length && sql[i] !== '\n') {
        out += ' '
        i++
      }
      continue
    }
    if (c === '/' && n === '*') {
      out += '  '
      i += 2
      while (i < sql.length && !(sql[i] === '*' && sql[i + 1] === '/')) {
        out += sql[i] === '\n' ? '\n' : ' '
        i++
      }
      if (i < sql.length) {
        out += '  '
        i += 2
      }
      continue
    }
    if (c === '\'' || c === '"') {
      const quote = c
      out += ' '
      i++
      while (i < sql.length) {
        if (sql[i] === '\\') {
          out += '  '
          i += 2
          continue
        }
        if (sql[i] === quote) {
          // SQL '' escape
          if (sql[i + 1] === quote) {
            out += '  '
            i += 2
            continue
          }
          out += ' '
          i++
          break
        }
        out += sql[i] === '\n' ? '\n' : ' '
        i++
      }
      continue
    }
    out += c
    i++
  }
  return out
}

/**
 * 按 `;` 取当前语句（含光标后），供 FROM 回看与 SELECT 前瞻。
 */
export function currentSqlStatement(fullText: string, offset: number): {
  text: string
  beforeCursor: string
  offsetInStatement: number
} {
  const safeOffset = Math.max(0, Math.min(offset, fullText.length))
  let start = 0
  const semiBefore = fullText.lastIndexOf(';', Math.max(0, safeOffset - 1))
  if (semiBefore >= 0) start = semiBefore + 1
  let end = fullText.length
  const semiAfter = fullText.indexOf(';', safeOffset)
  if (semiAfter >= 0) end = semiAfter
  return {
    text: fullText.slice(start, end),
    beforeCursor: fullText.slice(start, safeOffset),
    offsetInStatement: safeOffset - start,
  }
}

/**
 * 括号作用域链：从最内到最外（index 0 = 最内层），最后一项为整句。
 */
export function enclosingSqlScopes(statement: string, offsetInStatement: number): string[] {
  const cleaned = stripSqlNoise(statement)
  const safe = Math.max(0, Math.min(offsetInStatement, cleaned.length))
  const stack: number[] = []
  for (let i = 0; i < safe; i++) {
    if (cleaned[i] === '(') stack.push(i)
    else if (cleaned[i] === ')' && stack.length) stack.pop()
  }

  const scopes: string[] = []
  for (let i = stack.length - 1; i >= 0; i--) {
    const open = stack[i]
    let depth = 0
    let end = cleaned.length
    for (let j = open; j < cleaned.length; j++) {
      if (cleaned[j] === '(') depth++
      else if (cleaned[j] === ')') {
        depth--
        if (depth === 0) {
          end = j
          break
        }
      }
    }
    scopes.push(statement.slice(open + 1, end))
  }
  scopes.push(statement)
  return scopes
}

/**
 * 光标前最近子句决定补全槽：表 / 列 / 仅关键字。
 */
export function detectSqlSuggestSlot(textBeforeCursor: string): SqlSuggestSlot {
  const cleanedBefore = stripSqlNoise(textBeforeCursor)
  const trailingMatch = cleanedBefore.match(/[A-Za-z0-9_$#`]*$/u)
  const trailing = trailingMatch?.[0] || ''
  const trailingKw = stripTicks(trailing).toLowerCase()
  if (TABLE_SLOT_KEYWORDS.has(trailingKw)) return 'table_slot'
  if (COLUMN_SLOT_KEYWORDS.has(trailingKw)) return 'column_slot'

  const stripped = cleanedBefore.slice(0, cleanedBefore.length - trailing.length).trimEnd()
  if (!stripped) return 'keyword_only'

  // FROM ( / JOIN ( → 进入子查询，不推物理表
  if (/\b(?:from|join|into|update)\s*\(\s*$/i.test(stripped)) return 'keyword_only'

  // WITH cte AS ( → 关键字；WITH name 后可继续写 CTE
  if (/\bwith\s+$/i.test(stripped) || /\bwith\s+[A-Za-z0-9_$#`]+\s*$/i.test(stripped)) {
    return 'keyword_only'
  }

  const tokens = stripped.match(/[A-Za-z_][\w$#]*/g) || []
  if (!tokens.length) return 'keyword_only'

  for (let i = tokens.length - 1; i >= 0; i--) {
    const t = tokens[i].toLowerCase()
    if (TABLE_SLOT_KEYWORDS.has(t)) {
      const after = tokens.slice(i + 1).filter(x => !NOT_TABLE_ALIAS.has(x.toLowerCase()))
      if (after.length <= 2) return 'table_slot'
      return 'keyword_only'
    }
    if (COLUMN_SLOT_KEYWORDS.has(t)) return 'column_slot'
    if (t === 'insert' || t === 'delete' || t === 'create' || t === 'drop' || t === 'alter' || t === 'with') {
      return 'keyword_only'
    }
  }
  return 'keyword_only'
}

/** 简单 SELECT 列表投影列 / 别名（逗号深度 0）。 */
export function extractSelectListColumns(selectBody: string): string[] {
  const cleaned = stripSqlNoise(selectBody)
  const m = cleaned.match(/\bselect\b\s+([\s\S]*?)\s+\bfrom\b/i)
  const list = m ? m[1] : null
  if (!list) return []
  if (/^\s*\*\s*$/.test(list)) return []

  const cols: string[] = []
  let depth = 0
  let cur = ''
  for (let i = 0; i < list.length; i++) {
    const ch = list[i]
    if (ch === '(') depth++
    if (ch === ')') depth = Math.max(0, depth - 1)
    if (ch === ',' && depth === 0) {
      pushSelectItemAlias(cur, cols)
      cur = ''
      continue
    }
    cur += ch
  }
  pushSelectItemAlias(cur, cols)
  return cols
}

function pushSelectItemAlias(item: string, cols: string[]) {
  const t = item.trim()
  if (!t || t === '*') return
  const asMatch = t.match(/\bas\s+([A-Za-z0-9_$#`]+)\s*$/i)
  if (asMatch) {
    cols.push(stripTicks(asMatch[1]))
    return
  }
  // trailing bare ident: expr alias / plain col
  const bare = t.match(/([A-Za-z0-9_$#`]+)(?:\s*)?$/i)
  if (bare && !NOT_TABLE_ALIAS.has(stripTicks(bare[1]).toLowerCase())) {
    cols.push(stripTicks(bare[1]))
  }
}

/** 解析 WITH cte AS (...) [, cte2 AS (...)] */
export function extractCteDefinitions(statement: string): SqlCteDef[] {
  const cleaned = stripSqlNoise(statement)
  const withMatch = cleaned.match(/^\s*with\b/i)
  if (!withMatch) return []

  const defs: SqlCteDef[] = []
  let i = withMatch[0].length
  while (i < cleaned.length) {
    while (i < cleaned.length && /[\s,]/.test(cleaned[i])) i++
    const nameMatch = cleaned.slice(i).match(/^([A-Za-z0-9_$#`]+)/)
    if (!nameMatch) break
    const name = stripTicks(nameMatch[1])
    if (name.toLowerCase() === 'recursive') {
      i += nameMatch[0].length
      continue
    }
    if (NOT_TABLE_ALIAS.has(name.toLowerCase()) && name.toLowerCase() === 'select') break
    i += nameMatch[0].length
    while (i < cleaned.length && /\s/.test(cleaned[i])) i++
    // optional column list (a, b)
    if (cleaned[i] === '(') {
      let d = 0
      do {
        if (cleaned[i] === '(') d++
        else if (cleaned[i] === ')') d--
        i++
      } while (i < cleaned.length && d > 0)
      while (i < cleaned.length && /\s/.test(cleaned[i])) i++
    }
    const asMatch = cleaned.slice(i).match(/^as\b/i)
    if (!asMatch) break
    i += asMatch[0].length
    while (i < cleaned.length && /\s/.test(cleaned[i])) i++
    if (cleaned[i] !== '(') break
    const bodyStart = i + 1
    let depth = 0
    let bodyEnd = cleaned.length
    for (let j = i; j < cleaned.length; j++) {
      if (cleaned[j] === '(') depth++
      else if (cleaned[j] === ')') {
        depth--
        if (depth === 0) {
          bodyEnd = j
          i = j + 1
          break
        }
      }
    }
    const body = statement.slice(bodyStart, bodyEnd)
    const columns = extractSelectListColumns(body)
    defs.push({ name, body, columns })
    while (i < cleaned.length && /\s/.test(cleaned[i])) i++
    if (cleaned[i] === ',') {
      i++
      continue
    }
    break
  }
  return defs
}

function addRef(
  refs: SqlTableRef[],
  seen: Set<string>,
  ref: SqlTableRef,
) {
  const key = `${ref.kind || 'physical'}::${(ref.catalog || '').toLowerCase()}::${ref.table.toLowerCase()}::${(ref.alias || '').toLowerCase()}`
  if (seen.has(key)) return
  seen.add(key)
  refs.push(ref)
}

function parseTableToken(
  a: string,
  b: string | null,
  maybeAlias: string | null,
): SqlTableRef | null {
  if (!a || a === '(') return null
  if (NOT_TABLE_ALIAS.has(a.toLowerCase())) return null
  const catalog = b ? a : null
  const table = b || a
  const alias =
    maybeAlias && !NOT_TABLE_ALIAS.has(maybeAlias.toLowerCase()) ? maybeAlias : null
  return { catalog, table, alias, kind: 'physical' }
}

/**
 * 从一段 SQL 中抽取 FROM / JOIN / UPDATE / INTO 表引用（跳过 FROM ( 子查询起点）。
 */
export function extractTableRefs(fragment: string): SqlTableRef[] {
  const refs: SqlTableRef[] = []
  const seen = new Set<string>()
  const cleaned = stripSqlNoise(fragment)

  const re =
    /\b(?:from|join|update|into)\s+(?!\()([A-Za-z0-9_$#`]+)(?:\s*\.\s*([A-Za-z0-9_$#`]+))?(?:\s+(?:as\s+)?([A-Za-z0-9_$#`]+))?/gi

  let m: RegExpExecArray | null
  while ((m = re.exec(cleaned)) !== null) {
    const ref = parseTableToken(
      stripTicks(m[1]),
      m[2] ? stripTicks(m[2]) : null,
      m[3] ? stripTicks(m[3]) : null,
    )
    if (ref) addRef(refs, seen, ref)
  }

  // 子查询别名：FROM ( ... ) alias / JOIN ( ... ) alias（括号配对）
  const fromJoinRe = /\b(?:from|join)\s*\(/gi
  let fm: RegExpExecArray | null
  while ((fm = fromJoinRe.exec(cleaned)) !== null) {
    const open = fm.index + fm[0].length - 1
    let depth = 0
    let close = -1
    for (let j = open; j < cleaned.length; j++) {
      if (cleaned[j] === '(') depth++
      else if (cleaned[j] === ')') {
        depth--
        if (depth === 0) {
          close = j
          break
        }
      }
    }
    if (close < 0) continue
    const body = fragment.slice(open + 1, close)
    const after = cleaned.slice(close + 1).match(/^\s*(?:as\s+)?([A-Za-z0-9_$#`]+)/i)
    const aliasRaw = after ? stripTicks(after[1]) : null
    if (aliasRaw && NOT_TABLE_ALIAS.has(aliasRaw.toLowerCase())) {
      // 无有效别名：提升内层物理表
      for (const ir of extractTableRefs(body)) addRef(refs, seen, ir)
      continue
    }
    const cols = extractSelectListColumns(body)
    addRef(refs, seen, {
      catalog: null,
      table: aliasRaw || '_subquery',
      alias: aliasRaw,
      kind: 'subquery',
      columns: cols.length ? cols : undefined,
    })
    if (!aliasRaw) {
      for (const ir of extractTableRefs(body)) addRef(refs, seen, ir)
    }
  }

  // FROM a, b 逗号续表
  const fromChunk = cleaned.match(/\bfrom\b([^;]*?)(?=\bwhere\b|\bgroup\b|\border\b|\bhaving\b|\blimit\b|\bunion\b|$)/i)
  if (fromChunk) {
    const chunk = fromChunk[1]
    const parts = chunk.split(',')
    for (const part of parts) {
      if (/^\s*\(/.test(part)) continue
      const pm = part.match(
        /^\s*(?:(?:left|right|inner|outer|full|cross)\s+)*(?:join\s+)?([A-Za-z0-9_$#`]+)(?:\s*\.\s*([A-Za-z0-9_$#`]+))?(?:\s+(?:as\s+)?([A-Za-z0-9_$#`]+))?/i,
      )
      if (!pm) continue
      const ref = parseTableToken(
        stripTicks(pm[1]),
        pm[2] ? stripTicks(pm[2]) : null,
        pm[3] ? stripTicks(pm[3]) : null,
      )
      if (ref) addRef(refs, seen, ref)
    }
  }

  return refs
}

/**
 * 光标处可见表：最内层作用域优先，再叠加外层（关联子查询），并合并 CTE。
 */
export function resolveScopedTableRefs(
  statement: string,
  offsetInStatement: number,
): { refs: SqlTableRef[]; ctes: SqlCteDef[] } {
  const ctes = extractCteDefinitions(statement)
  const cteByName = new Map(ctes.map(c => [c.name.toLowerCase(), c]))
  const scopes = enclosingSqlScopes(statement, offsetInStatement)
  const seen = new Set<string>()
  const refs: SqlTableRef[] = []

  // 从最内到最外合并（内层优先出现在列表前）
  for (const scope of scopes) {
    for (const ref of extractTableRefs(scope)) {
      const cte = cteByName.get(ref.table.toLowerCase())
      if (cte && !ref.catalog) {
        addRef(refs, seen, {
          catalog: null,
          table: cte.name,
          alias: ref.alias,
          kind: 'cte',
          columns: cte.columns.length ? cte.columns : undefined,
        })
        // CTE 无投影列时，回落到底表列
        if (!cte.columns.length) {
          for (const inner of extractTableRefs(cte.body)) {
            addRef(refs, seen, { ...inner, alias: ref.alias || inner.alias })
          }
        }
        continue
      }
      addRef(refs, seen, ref)
    }
  }

  return { refs, ctes }
}

/**
 * 当前语句 SELECT 列表别名（供 ORDER BY / GROUP BY，业界常见）。
 */
export function extractOuterSelectAliases(
  statement: string,
  offsetInStatement: number,
): string[] {
  const scopes = enclosingSqlScopes(statement, offsetInStatement)
  // 最外层主查询（最后一个 scope 是整句）；若在子查询内则用最内层
  const target = scopes[0] || statement
  return extractSelectListColumns(target)
}

/**
 * 解析 `db.table.` / `foo.` / 裸前缀。
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

function filterNames(names: string[], prefix: string): string[] {
  const p = prefix.toLowerCase()
  if (!p) return names
  return names.filter(n => n.toLowerCase().startsWith(p) || n.toLowerCase().includes(p))
}

function pushKeywords(
  suggestions: any[],
  Kind: any,
  range: any,
  prefix: string,
) {
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

async function pushSchemasAndTables(
  suggestions: any[],
  Kind: any,
  range: any,
  dsId: number,
  defaultCatalog: string | null,
  prefix: string,
  ctes: SqlCteDef[],
) {
  for (const cte of ctes) {
    if (prefix && !cte.name.toLowerCase().startsWith(prefix.toLowerCase())
      && !cte.name.toLowerCase().includes(prefix.toLowerCase())) {
      continue
    }
    suggestions.push({
      label: cte.name,
      kind: Kind.Class,
      detail: 'CTE',
      insertText: cte.name,
      range,
      sortText: `0_${cte.name}`,
    })
  }
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
}

async function pushColumnsForRefs(
  suggestions: any[],
  Kind: any,
  range: any,
  dsId: number,
  defaultCatalog: string | null,
  refs: SqlTableRef[],
  prefix: string,
  selectAliases: string[],
) {
  const multi = refs.length > 1
  const seenCol = new Set<string>()

  for (const name of filterNames(selectAliases, prefix)) {
    const key = name.toLowerCase()
    if (seenCol.has(key)) continue
    seenCol.add(key)
    suggestions.push({
      label: name,
      kind: Kind.Field,
      detail: 'select alias',
      insertText: name,
      range,
      sortText: `0_${name}`,
    })
  }

  for (const ref of refs) {
    const qual = ref.alias || (multi ? ref.table : null)

    if (ref.columns?.length) {
      for (const name of filterNames(ref.columns, prefix)) {
        const insert = qual ? `${qual}.${name}` : name
        const key = insert.toLowerCase()
        if (seenCol.has(key)) continue
        seenCol.add(key)
        suggestions.push({
          label: insert,
          kind: Kind.Field,
          detail: ref.kind === 'cte' ? 'CTE column' : 'subquery column',
          insertText: insert,
          range,
          sortText: `0_${name}`,
        })
      }
      continue
    }

    if (ref.kind === 'cte' || ref.kind === 'subquery') continue

    const catalog = ref.catalog || defaultCatalog
    const columns = filterByPrefix(await fetchColumns(dsId, ref.table, catalog), prefix)
    for (const c of columns) {
      const insert = qual ? `${qual}.${c.name}` : c.name
      const key = insert.toLowerCase()
      if (seenCol.has(key)) continue
      seenCol.add(key)
      suggestions.push({
        label: insert,
        kind: Kind.Field,
        detail: c.type || `${ref.table} column`,
        insertText: insert,
        range,
        sortText: `0_${c.name}`,
      })
    }
  }
}

function resolveAliasOrTable(
  left: string,
  refs: SqlTableRef[],
  ctes: SqlCteDef[],
): { catalog: string | null; table: string; columns?: string[]; virtual?: boolean } | null {
  const lower = left.toLowerCase()
  for (const ref of refs) {
    if (ref.alias && ref.alias.toLowerCase() === lower) {
      return {
        catalog: ref.catalog,
        table: ref.table,
        columns: ref.columns,
        virtual: ref.kind === 'cte' || ref.kind === 'subquery',
      }
    }
    if (ref.table.toLowerCase() === lower) {
      return {
        catalog: ref.catalog,
        table: ref.table,
        columns: ref.columns,
        virtual: ref.kind === 'cte' || ref.kind === 'subquery',
      }
    }
  }
  const cte = ctes.find(c => c.name.toLowerCase() === lower)
  if (cte) return { catalog: null, table: cte.name, columns: cte.columns, virtual: true }
  return null
}

export type BindSqlCompletionOpts = {
  getDatasourceId: () => number | null | undefined
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
      const fullText = String(model.getValue?.() ?? '')
      const offset = typeof model.getOffsetAt === 'function'
        ? Number(model.getOffsetAt(position))
        : 0
      const {
        text: statement,
        beforeCursor: stmtBefore,
        offsetInStatement,
      } = currentSqlStatement(fullText, offset)
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
      const prefixLen = ctx.prefix.length
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
      const { refs, ctes } = resolveScopedTableRefs(statement, offsetInStatement)
      const selectAliases = extractOuterSelectAliases(statement, offsetInStatement)

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
            const resolved = resolveAliasOrTable(ctx.left, refs, ctes)
            if (resolved?.columns?.length) {
              for (const name of filterNames(resolved.columns, ctx.prefix)) {
                suggestions.push({
                  label: name,
                  kind: Kind.Field,
                  detail: 'column',
                  insertText: name,
                  range: dotRange,
                  sortText: `0_${name}`,
                })
              }
            } else if (!resolved?.virtual) {
              const table = resolved?.table || ctx.left
              const catalog = resolved?.catalog || defaultCatalog
              const columns = filterByPrefix(
                await fetchColumns(dsId, table, catalog),
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
          }
        } else {
          const prefix = ctx.prefix
          const slot = detectSqlSuggestSlot(stmtBefore || before)
          if (slot === 'table_slot') {
            await pushSchemasAndTables(
              suggestions, Kind, range, dsId, defaultCatalog, prefix, ctes,
            )
            pushKeywords(suggestions, Kind, range, prefix)
          } else if (slot === 'column_slot') {
            await pushColumnsForRefs(
              suggestions, Kind, range, dsId, defaultCatalog, refs, prefix, selectAliases,
            )
            pushKeywords(suggestions, Kind, range, prefix)
          } else {
            pushKeywords(suggestions, Kind, range, prefix)
            if (prefix.length >= 2) {
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
            }
          }
        }
      } catch {
        // 补全失败静默
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
