/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-08-10
 *
 * 批开发 Studio / 实时 Stream Studio / 数据探查 Probe 共用工作台壳：
 * 全高 bleed + 左侧目录树 chrome + 右侧 flex 列。业务条（Tab / 工具栏 / 编辑器）走插槽。
 */
import type { CSSProperties, ReactNode } from 'react'
import { Button, Space, Tooltip } from 'antd'
import { MenuUnfoldOutlined } from '@ant-design/icons'
import ResizableSidebar from './ResizableSidebar'

/** 抵消 Content padding，铺满视口工作区 */
export const STUDIO_WORKBENCH_BLEED_STYLE: CSSProperties = {
  height: 'calc(100vh - 112px)',
  margin: -24,
  overflow: 'hidden',
}

const LEFT_ROOT: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  background: '#fafafa',
  height: '100%',
  minHeight: 0,
}

const LEFT_HEADER: CSSProperties = {
  padding: '10px 12px',
  borderBottom: '1px solid #f0f0f0',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
}

const LEFT_TITLE: CSSProperties = {
  fontWeight: 600,
  fontSize: 13,
}

const LEFT_BODY: CSSProperties = {
  flex: 1,
  overflow: 'auto',
  padding: '4px 0',
}

const RIGHT_ROOT: CSSProperties = {
  height: '100%',
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}

type ShellProps = {
  storageKey: string
  defaultWidth?: number
  minWidth?: number
  maxWidth?: number
  collapsed: boolean
  sidebarTitle: ReactNode
  /** 左侧标题栏操作（新建 / 折叠等），末尾建议放「隐藏列表」 */
  sidebarActions?: ReactNode
  sidebarClassName?: string
  treeBodyClassName?: string
  tree: ReactNode
  children: ReactNode
}

/** 工作台外框：左侧树 + 右侧舞台 */
export default function StudioWorkbenchShell({
  storageKey,
  defaultWidth = 240,
  minWidth = 180,
  maxWidth = 560,
  collapsed,
  sidebarTitle,
  sidebarActions,
  sidebarClassName,
  treeBodyClassName,
  tree,
  children,
}: ShellProps) {
  return (
    <ResizableSidebar
      storageKey={storageKey}
      defaultWidth={defaultWidth}
      minWidth={minWidth}
      maxWidth={maxWidth}
      collapsed={collapsed}
      style={STUDIO_WORKBENCH_BLEED_STYLE}
      left={(
        <div className={sidebarClassName} style={LEFT_ROOT}>
          <div style={LEFT_HEADER}>
            <span style={LEFT_TITLE}>{sidebarTitle}</span>
            {sidebarActions != null && <Space size={0}>{sidebarActions}</Space>}
          </div>
          <div style={LEFT_BODY} className={treeBodyClassName}>
            {tree}
          </div>
        </div>
      )}
      right={<div style={RIGHT_ROOT}>{children}</div>}
    />
  )
}

/** 顶栏：多 Tab 或当前实体标题 */
export function StudioWorkbenchTopStrip({
  children,
  style,
  padded,
}: {
  children: ReactNode
  style?: CSSProperties
  /** 标题行常用左右内边距；多 Tab 行一般不加 */
  padded?: boolean
}) {
  return (
    <div
      style={{
        borderBottom: '1px solid #f0f0f0',
        background: '#fff',
        display: 'flex',
        alignItems: 'center',
        minHeight: 40,
        flexShrink: 0,
        overflowX: 'auto',
        padding: padded ? '0 12px' : undefined,
        gap: padded ? 8 : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  )
}

/** 工具栏：运行 / 保存 / 发布等 */
export function StudioWorkbenchToolbar({
  children,
  wrap,
}: {
  children: ReactNode
  wrap?: boolean
}) {
  return (
    <div
      style={{
        padding: '6px 12px',
        borderBottom: '1px solid #f0f0f0',
        background: '#fff',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexShrink: 0,
        // 默认横向滚动，避免 Tag/按钮显隐导致折行把下方编辑区顶来顶去
        flexWrap: wrap ? 'wrap' : 'nowrap',
        overflowX: wrap ? undefined : 'auto',
        minHeight: 40,
      }}
    >
      {children}
    </div>
  )
}

/** 编辑器 + 结果分栏的主舞台 */
export function StudioWorkbenchStage({ children }: { children: ReactNode }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      {children}
    </div>
  )
}

/** 未选中实体时的空态 */
export function StudioWorkbenchEmpty({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'column',
        gap: 12,
        color: '#666',
        background: '#fafafa',
        padding: 32,
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  )
}

/** 侧栏折叠后，在顶栏展开列表 */
export function StudioWorkbenchExpandSidebarButton({
  collapsed,
  onExpand,
  tooltip,
}: {
  collapsed: boolean
  onExpand: () => void
  tooltip: string
}) {
  if (!collapsed) return null
  return (
    <Tooltip title={tooltip}>
      <Button
        type="text"
        size="small"
        icon={<MenuUnfoldOutlined />}
        onClick={onExpand}
        style={{ marginLeft: 4, flexShrink: 0 }}
      />
    </Tooltip>
  )
}
