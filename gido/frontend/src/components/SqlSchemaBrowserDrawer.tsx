/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe 共用：库表浏览器抽屉（搜索表、展开列、双击插入）。
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type Key, type MouseEvent, type ReactNode } from 'react'
import { Drawer, Input, Spin, Tag, Tooltip, Tree, Typography, message } from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  DatabaseOutlined,
  TableOutlined,
  FieldStringOutlined,
} from '@ant-design/icons'
import {
  fetchColumns,
  fetchSchemas,
  fetchTables,
  type ColumnHint,
  type TableHint,
} from '../utils/sqlSchemaCache'
import './SqlSchemaBrowserDrawer.css'

type NodeKind = 'schema' | 'table' | 'column'

type SchemaNodeData = {
  kind: 'schema'
  name: string
  isDefault?: boolean
}

type TableNodeData = {
  kind: 'table'
  catalog: string
  name: string
  comment?: string
}

type ColumnNodeData = {
  kind: 'column'
  catalog: string
  table: string
  name: string
  type?: string
}

type SchemaTreeNode = DataNode & {
  meta?: SchemaNodeData | TableNodeData | ColumnNodeData
}

type Props = {
  open: boolean
  onClose: () => void
  datasourceId: number | null | undefined
  defaultCatalog?: string | null
  onInsert: (text: string) => void
}

const MONO: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
}

export default function SqlSchemaBrowserDrawer({
  open,
  onClose,
  datasourceId,
  defaultCatalog,
  onInsert,
}: Props) {
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [treeData, setTreeData] = useState<SchemaTreeNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])
  const [preferredCatalog, setPreferredCatalog] = useState<string>('')

  const loadRoot = useCallback(async () => {
    if (!datasourceId || !open) return
    setLoading(true)
    try {
      const schemas = await fetchSchemas(datasourceId)
      const preferred = (defaultCatalog || schemas.find(s => s.is_default)?.name || schemas[0]?.name || '').trim()
      setPreferredCatalog(preferred)
      const nodes: SchemaTreeNode[] = schemas.map(s => ({
        key: `s:${s.name}`,
        title: s.name,
        isLeaf: false,
        selectable: false,
        meta: { kind: 'schema', name: s.name, isDefault: Boolean(s.is_default) },
      }))
      setTreeData(nodes)
      if (preferred) {
        setExpandedKeys([`s:${preferred}`])
        const tables = await fetchTables(datasourceId, preferred, keyword)
        setTreeData(prev => attachTables(prev, preferred, tables))
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '加载库表失败')
    } finally {
      setLoading(false)
    }
  }, [datasourceId, open, defaultCatalog, keyword])

  useEffect(() => {
    if (open) void loadRoot()
  }, [open, loadRoot])

  useEffect(() => {
    if (!open) {
      setKeyword('')
      setTreeData([])
      setExpandedKeys([])
    }
  }, [open])

  const onLoadData = async (node: any) => {
    if (!datasourceId) return
    const key = String(node.key)
    if (key.startsWith('s:')) {
      const catalog = key.slice(2)
      const tables = await fetchTables(datasourceId, catalog, keyword)
      setTreeData(prev => attachTables(prev, catalog, tables))
      return
    }
    if (key.startsWith('t:')) {
      const rest = key.slice(2)
      const idx = rest.indexOf(':')
      const catalog = rest.slice(0, idx)
      const table = rest.slice(idx + 1)
      const cols = await fetchColumns(datasourceId, table, catalog)
      setTreeData(prev => attachColumns(prev, catalog, table, cols))
    }
  }

  const treeHeight = useMemo(() => Math.max(360, Math.min(640, window.innerHeight - 220)), [open])

  const drawerTitle = (
    <div className="sql-schema-browser-title">
      <span className="sql-schema-browser-title-main">库表</span>
      {preferredCatalog ? (
        <span className="sql-schema-browser-title-sub">
          默认库 <code style={MONO}>{preferredCatalog}</code>
        </span>
      ) : null}
    </div>
  )

  return (
    <Drawer
      title={drawerTitle}
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      destroyOnClose
      className="sql-schema-browser-drawer"
      styles={{
        body: { padding: '12px 16px 16px', display: 'flex', flexDirection: 'column', height: '100%' },
      }}
    >
      {!datasourceId ? (
        <div className="sql-schema-browser-empty">
          <Typography.Text type="secondary">请先绑定数据源后再浏览库表</Typography.Text>
        </div>
      ) : (
        <>
          <Input.Search
            allowClear
            placeholder="过滤表名"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={() => void loadRoot()}
            className="sql-schema-browser-search"
          />
          <div className="sql-schema-browser-hint">
            双击表名插入 <code>catalog.table</code>
            <span className="sql-schema-browser-hint-sep">·</span>
            双击列名插入列名
          </div>
          <Spin spinning={loading} className="sql-schema-browser-spin">
            <Tree
              className="sql-schema-browser-tree"
              treeData={treeData}
              loadData={onLoadData}
              expandedKeys={expandedKeys}
              onExpand={keys => setExpandedKeys(keys)}
              height={treeHeight}
              blockNode
              titleRender={(node) => {
                const n = node as SchemaTreeNode
                return (
                  <SchemaTreeTitle
                    node={n}
                    onInsert={(text, okMsg) => {
                      onInsert(text)
                      message.success(okMsg)
                    }}
                  />
                )
              }}
            />
          </Spin>
        </>
      )}
    </Drawer>
  )
}

function SchemaTreeTitle({
  node,
  onInsert,
}: {
  node: SchemaTreeNode
  onInsert: (text: string, okMsg: string) => void
}) {
  const meta = node.meta
  const key = String(node.key)

  const handleDoubleClick = (e: MouseEvent) => {
    e.stopPropagation()
    if (!meta) return
    if (meta.kind === 'table') {
      const text = `${meta.catalog}.${meta.name}`
      onInsert(text, `已插入 ${text}`)
    } else if (meta.kind === 'column') {
      onInsert(meta.name, `已插入 ${meta.name}`)
    }
  }

  let icon: ReactNode = null
  let primary: ReactNode = node.title as ReactNode
  let secondary: ReactNode = null
  let trailing: ReactNode = null
  let insertable = false

  if (meta?.kind === 'schema') {
    icon = <DatabaseOutlined className="sql-schema-browser-icon schema" />
    primary = <span className="sql-schema-browser-name">{meta.name}</span>
    trailing = meta.isDefault ? <Tag className="sql-schema-browser-default-tag">默认</Tag> : null
  } else if (meta?.kind === 'table') {
    insertable = true
    icon = <TableOutlined className="sql-schema-browser-icon table" />
    primary = (
      <span className="sql-schema-browser-name mono" title={meta.name}>
        {meta.name}
      </span>
    )
    if (meta.comment) {
      secondary = (
        <Tooltip title={meta.comment} placement="left">
          <span className="sql-schema-browser-comment">{meta.comment}</span>
        </Tooltip>
      )
    }
  } else if (meta?.kind === 'column') {
    insertable = true
    icon = <FieldStringOutlined className="sql-schema-browser-icon column" />
    primary = (
      <span className="sql-schema-browser-name mono" title={meta.name}>
        {meta.name}
      </span>
    )
    if (meta.type) {
      trailing = <span className="sql-schema-browser-type">{meta.type}</span>
    }
  } else if (key.startsWith('t:')) {
    // fallback for nodes without meta
    insertable = true
    primary = <span className="sql-schema-browser-name mono">{String(node.title)}</span>
  }

  return (
    <div
      className={`sql-schema-browser-row${insertable ? ' insertable' : ''}`}
      onDoubleClick={handleDoubleClick}
      title={insertable ? '双击插入到编辑器' : undefined}
    >
      <div className="sql-schema-browser-row-main">
        {icon}
        <div className="sql-schema-browser-row-text">
          <div className="sql-schema-browser-row-line">{primary}</div>
          {secondary ? <div className="sql-schema-browser-row-line secondary">{secondary}</div> : null}
        </div>
        {trailing}
      </div>
    </div>
  )
}

function attachTables(prev: SchemaTreeNode[], catalog: string, tables: TableHint[]): SchemaTreeNode[] {
  return prev.map(n => {
    if (n.key !== `s:${catalog}`) return n
    return {
      ...n,
      children: tables.map(t => ({
        key: `t:${catalog}:${t.name}`,
        title: t.name,
        isLeaf: false,
        meta: {
          kind: 'table' as const,
          catalog,
          name: t.name,
          comment: t.comment || undefined,
        },
      })),
    }
  })
}

function attachColumns(
  prev: SchemaTreeNode[],
  catalog: string,
  table: string,
  cols: ColumnHint[],
): SchemaTreeNode[] {
  const tKey = `t:${catalog}:${table}`
  return prev.map(n => {
    if (n.key !== `s:${catalog}` || !n.children) return n
    return {
      ...n,
      children: n.children.map(ch => {
        if (ch.key !== tKey) return ch
        return {
          ...ch,
          children: cols.map(c => ({
            key: `c:${catalog}:${table}:${c.name}`,
            title: c.name,
            isLeaf: true,
            meta: {
              kind: 'column' as const,
              catalog,
              table,
              name: c.name,
              type: c.type || undefined,
            },
          })),
        }
      }),
    }
  })
}
