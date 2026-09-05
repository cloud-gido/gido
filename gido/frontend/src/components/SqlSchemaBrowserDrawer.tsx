/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe 共用：库表浏览器抽屉（搜索表、展开列、双击插入）。
 */
import { useCallback, useEffect, useState, type Key } from 'react'
import { Drawer, Input, Spin, Tree, Typography, message } from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  fetchColumns,
  fetchSchemas,
  fetchTables,
  type ColumnHint,
  type TableHint,
} from '../utils/sqlSchemaCache'

type Props = {
  open: boolean
  onClose: () => void
  datasourceId: number | null | undefined
  defaultCatalog?: string | null
  onInsert: (text: string) => void
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
  const [treeData, setTreeData] = useState<DataNode[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])

  const loadRoot = useCallback(async () => {
    if (!datasourceId || !open) return
    setLoading(true)
    try {
      const schemas = await fetchSchemas(datasourceId)
      const preferred = (defaultCatalog || schemas.find(s => s.is_default)?.name || schemas[0]?.name || '').trim()
      const nodes: DataNode[] = []
      for (const s of schemas) {
        nodes.push({
          key: `s:${s.name}`,
          title: s.name + (s.is_default ? ' (默认)' : ''),
          isLeaf: false,
          selectable: false,
        })
      }
      setTreeData(nodes)
      if (preferred) {
        setExpandedKeys([`s:${preferred}`])
        // 预加载默认库表
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
      // t:catalog:table
      const rest = key.slice(2)
      const idx = rest.indexOf(':')
      const catalog = rest.slice(0, idx)
      const table = rest.slice(idx + 1)
      const cols = await fetchColumns(datasourceId, table, catalog)
      setTreeData(prev => attachColumns(prev, catalog, table, cols))
    }
  }

  return (
    <Drawer
      title="库表"
      placement="right"
      width={360}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {!datasourceId ? (
        <Typography.Text type="secondary">请先绑定数据源后再浏览库表</Typography.Text>
      ) : (
        <>
          <Input.Search
            allowClear
            placeholder="过滤表名"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={() => void loadRoot()}
            style={{ marginBottom: 12 }}
          />
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
            双击表名插入 catalog.table；双击列名插入列
          </Typography.Paragraph>
          <Spin spinning={loading}>
            <Tree
              treeData={treeData}
              loadData={onLoadData}
              expandedKeys={expandedKeys}
              onExpand={keys => setExpandedKeys(keys)}
              height={480}
              titleRender={(node) => {
                const key = String(node.key)
                const isTable = key.startsWith('t:')
                const isCol = key.startsWith('c:')
                return (
                  <span
                    onDoubleClick={(e) => {
                      e.stopPropagation()
                      if (isTable) {
                        const rest = key.slice(2)
                        const i = rest.indexOf(':')
                        const catalog = rest.slice(0, i)
                        const table = rest.slice(i + 1)
                        onInsert(`${catalog}.${table}`)
                        message.success(`已插入 ${catalog}.${table}`)
                      } else if (isCol) {
                        const parts = key.split(':')
                        const col = parts[parts.length - 1]
                        onInsert(col)
                        message.success(`已插入 ${col}`)
                      }
                    }}
                    style={{ userSelect: 'none' }}
                  >
                    {node.title as any}
                  </span>
                )
              }}
            />
          </Spin>
        </>
      )}
    </Drawer>
  )
}

function attachTables(prev: DataNode[], catalog: string, tables: TableHint[]): DataNode[] {
  return prev.map(n => {
    if (n.key !== `s:${catalog}`) return n
    return {
      ...n,
      children: tables.map(t => ({
        key: `t:${catalog}:${t.name}`,
        title: t.comment ? `${t.name}  — ${t.comment}` : t.name,
        isLeaf: false,
      })),
    }
  })
}

function attachColumns(
  prev: DataNode[],
  catalog: string,
  table: string,
  cols: ColumnHint[],
): DataNode[] {
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
            title: c.type ? `${c.name} : ${c.type}` : c.name,
            isLeaf: true,
          })),
        }
      }),
    }
  })
}
