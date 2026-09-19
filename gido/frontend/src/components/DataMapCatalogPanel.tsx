/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图左侧：与 Studio「库表」同源的库→表→列懒加载树（sqlSchemaCache）。
 */
import { useCallback, useEffect, useMemo, useState, type Key, type ReactNode } from 'react'
import { Input, Spin, Tag, Tooltip, Tree, Typography } from 'antd'
import {
  DatabaseOutlined,
  TableOutlined,
  FieldStringOutlined,
} from '@ant-design/icons'
import { fetchColumns, fetchSchemas, fetchTables, invalidateSqlSchemaCache } from '../utils/sqlSchemaCache'
import {
  attachColumns,
  attachTables,
  schemaKey,
  type SchemaTreeNode,
} from '../utils/sqlSchemaTree'
import './SqlSchemaBrowserDrawer.css'
import './DataMapCatalogPanel.css'

export type DataMapTableSelection = {
  datasourceId: number
  catalog: string
  tableName: string
  comment?: string
  metaTableId?: number
  registered?: boolean
}

type Props = {
  datasourceId: number | null | undefined
  defaultCatalog?: string | null
  /** catalog|table → meta id */
  registeredMap: Map<string, number>
  selectedKey?: string | null
  locateCatalog?: string | null
  locateTable?: string | null
  onSelectTable: (sel: DataMapTableSelection) => void
  refreshToken?: number
}

function regLookupKey(catalog: string, table: string) {
  return `${String(catalog || '').trim()}|${String(table || '').trim()}`
}

export default function DataMapCatalogPanel({
  datasourceId,
  defaultCatalog,
  registeredMap,
  selectedKey,
  locateCatalog,
  locateTable,
  onSelectTable,
  refreshToken = 0,
}: Props) {
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [treeData, setTreeData] = useState<SchemaTreeNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])

  const lookupReg = useCallback(
    (catalog: string, table: string) => {
      const id = registeredMap.get(regLookupKey(catalog, table))
      return id != null ? { id } : undefined
    },
    [registeredMap],
  )

  const loadRoot = useCallback(async () => {
    if (!datasourceId) {
      setTreeData([])
      return
    }
    setLoading(true)
    try {
      const schemas = await fetchSchemas(datasourceId)
      const preferred = (defaultCatalog || schemas.find((s) => s.is_default)?.name || schemas[0]?.name || '').trim()
      const nodes: SchemaTreeNode[] = schemas.map((s) => ({
        key: schemaKey(s.name),
        title: s.name,
        isLeaf: false,
        selectable: false,
        meta: { kind: 'schema', name: s.name, isDefault: Boolean(s.is_default) },
      }))
      setTreeData(nodes)
      if (preferred) {
        setExpandedKeys([schemaKey(preferred)])
        const tables = await fetchTables(datasourceId, preferred, keyword)
        setTreeData((prev) => attachTables(prev, preferred, tables, lookupReg))
      }
    } catch (e: any) {
      setTreeData([])
      throw e
    } finally {
      setLoading(false)
    }
  }, [datasourceId, defaultCatalog, keyword, lookupReg])

  useEffect(() => {
    if (!datasourceId) return
    void loadRoot().catch(() => undefined)
  }, [datasourceId, loadRoot, refreshToken])

  // 收录状态变化时刷新已挂载表节点，供单击后打开右侧字典；树上不再画角标
  useEffect(() => {
    setTreeData((prev) =>
      prev.map((n) => {
        if (!n.children || n.meta?.kind !== 'schema') return n
        const catalog = n.meta.name
        return {
          ...n,
          children: n.children.map((ch) => {
            if (ch.meta?.kind !== 'table') return ch
            const reg = lookupReg(catalog, ch.meta.name)
            return {
              ...ch,
              meta: {
                ...ch.meta,
                registered: Boolean(reg),
                metaTableId: reg?.id,
              },
            }
          }),
        }
      }),
    )
  }, [registeredMap, lookupReg])

  const onLoadData = async (node: any) => {
    if (!datasourceId) return
    const key = String(node.key)
    if (key.startsWith('s:')) {
      const catalog = key.slice(2)
      const tables = await fetchTables(datasourceId, catalog, keyword)
      setTreeData((prev) => attachTables(prev, catalog, tables, lookupReg))
      return
    }
    if (key.startsWith('t:')) {
      const rest = key.slice(2)
      const idx = rest.indexOf(':')
      const catalog = rest.slice(0, idx)
      const table = rest.slice(idx + 1)
      const cols = await fetchColumns(datasourceId, table, catalog)
      setTreeData((prev) => attachColumns(prev, catalog, table, cols))
    }
  }

  useEffect(() => {
    if (!datasourceId || !locateCatalog) return
    const key = schemaKey(locateCatalog)
    setExpandedKeys((prev) => (prev.includes(key) ? prev : [...prev, key]))
    let cancelled = false
    void fetchTables(datasourceId, locateCatalog).then((tables) => {
      if (cancelled) return
      setTreeData((prev) => attachTables(prev, locateCatalog, tables, lookupReg))
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [datasourceId, locateCatalog, locateTable, lookupReg])

  const treeHeight = useMemo(() => Math.max(420, window.innerHeight - 260), [refreshToken])

  if (!datasourceId) {
    return (
      <div className="datamap-catalog-panel sql-schema-browser-empty">
        <Typography.Text type="secondary">请先选择数据源</Typography.Text>
      </div>
    )
  }

  return (
    <div className="datamap-catalog-panel">
      <Input.Search
        allowClear
        placeholder="过滤表名"
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        onSearch={() => {
          invalidateSqlSchemaCache(datasourceId)
          void loadRoot().catch(() => undefined)
        }}
        className="sql-schema-browser-search"
      />
      <div className="sql-schema-browser-hint">
        与数据开发「库表」同源枚举。单击表打开右侧字典；展开可见字段注释。
      </div>
      <Spin spinning={loading} className="sql-schema-browser-spin">
        <Tree
          className="sql-schema-browser-tree"
          treeData={treeData}
          loadData={onLoadData}
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys as Key[])}
          selectedKeys={selectedKey ? [selectedKey] : []}
          height={treeHeight}
          blockNode
          onSelect={(keys, info) => {
            const meta = (info.node as SchemaTreeNode).meta
            if (!meta || meta.kind !== 'table' || !datasourceId) return
            onSelectTable({
              datasourceId,
              catalog: meta.catalog,
              tableName: meta.name,
              comment: meta.comment,
              metaTableId: meta.metaTableId,
              registered: meta.registered,
            })
          }}
          titleRender={(node) => {
            const n = node as SchemaTreeNode
            return <CatalogTreeTitle node={n} />
          }}
        />
      </Spin>
    </div>
  )
}

function CatalogTreeTitle({ node }: { node: SchemaTreeNode }) {
  const meta = node.meta
  let icon: ReactNode = null
  let primary: ReactNode = node.title as ReactNode
  let secondary: ReactNode = null
  let trailing: ReactNode = null

  if (meta?.kind === 'schema') {
    icon = <DatabaseOutlined className="sql-schema-browser-icon schema" />
    primary = <span className="sql-schema-browser-name">{meta.name}</span>
    trailing = meta.isDefault ? <Tag className="sql-schema-browser-default-tag">默认</Tag> : null
  } else if (meta?.kind === 'table') {
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
    icon = <FieldStringOutlined className="sql-schema-browser-icon column" />
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
    if (meta.type) {
      trailing = <span className="sql-schema-browser-type">{meta.type}</span>
    }
  }

  return (
    <div className="sql-schema-browser-row">
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
