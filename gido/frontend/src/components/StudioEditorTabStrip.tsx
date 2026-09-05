/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据开发编辑器 Tab 条（会话壳 / 懒加载铬态），供 Studio 挂载与 E2E 覆盖。
 * 业界对齐（VS Code / Chrome / Notepad++）：
 * - 自有横向滚动容器，激活时只滚到「完整可见」（不居中、不滚祖先）
 * - 右侧 ▾ 溢出列表展示全名，便于多 Tab 切换
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { Button, Dropdown, Tag, Tooltip } from 'antd'
import {
  CheckOutlined,
  CloseCircleOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  LeftOutlined,
  LoadingOutlined,
  ReloadOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { isEditorTabContentPending } from '../utils/editorSessionStore'
import {
  resolveStudioTabChrome,
  studioTabTitleColor,
  studioTabTitleItalic,
} from '../utils/studioTabChrome'
import { ensureChildFullyVisibleHorizontally } from '../utils/studioTabScroll'

const TYPE_COLOR: Record<string, string> = {
  SQL: 'blue', PYTHON: 'green', SHELL: 'orange', SYNC: 'purple', VIRTUAL: 'default', DEPENDENT: 'magenta',
}

/** 状态铬固定槽宽，避免 loading / 脏点显隐把右侧 Tab 挤来挤去 */
const STATUS_SLOT_STYLE: CSSProperties = {
  width: 12,
  height: 12,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  fontSize: 11,
  lineHeight: 1,
}

const SCROLL_BTN_STYLE: CSSProperties = {
  width: 28,
  height: 40,
  padding: 0,
  flexShrink: 0,
  borderRadius: 0,
  border: 'none',
  borderLeft: '1px solid #f0f0f0',
  color: '#8c8c8c',
}

export type StudioEditorTabModel = {
  id: number
  name: string
  node_type: string
  script_content?: string | null
}

type Props = {
  tabs: StudioEditorTabModel[]
  activeTabId: number | null
  /** 相对「保存版本」的脏标记；勿用静默草稿 dirty，否则 autosave 会让 ● 闪烁带动布局抖 */
  versionDirtyMap: Record<number, boolean | undefined>
  tabContentLoading: Record<number, boolean>
  tabContentError: Record<number, string>
  onActivate: (tabId: number) => void
  onClose: (tabId: number) => void
  onReload: (tabId: number) => void
  onCloseOthers: (tabId: number) => void
  onCloseLeft: (tabId: number) => void
  onCloseRight: (tabId: number) => void
  onCloseAll: () => void
}

function TabStatusSlot({
  loading,
  error,
  dirty,
  tabId,
}: {
  loading: boolean
  error?: string
  dirty: boolean
  tabId: number
}) {
  if (loading) {
    return (
      <span style={STATUS_SLOT_STYLE} aria-hidden>
        <LoadingOutlined data-testid={`studio-tab-loading-${tabId}`} style={{ fontSize: 11, color: '#1677ff' }} />
      </span>
    )
  }
  if (error) {
    return (
      <span style={STATUS_SLOT_STYLE} aria-hidden>
        <ExclamationCircleOutlined
          data-testid={`studio-tab-error-${tabId}`}
          style={{ fontSize: 11, color: '#d48806' }}
        />
      </span>
    )
  }
  return (
    <span
      data-testid={`studio-tab-dirty-${tabId}`}
      style={{
        ...STATUS_SLOT_STYLE,
        color: '#faad14',
        fontSize: 10,
        visibility: dirty ? 'visible' : 'hidden',
      }}
      aria-hidden={!dirty}
    >
      ●
    </span>
  )
}

export default function StudioEditorTabStrip({
  tabs,
  activeTabId,
  versionDirtyMap,
  tabContentLoading,
  tabContentError,
  onActivate,
  onClose,
  onReload,
  onCloseOthers,
  onCloseLeft,
  onCloseRight,
  onCloseAll,
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const activeTabElRef = useRef<HTMLDivElement | null>(null)
  const [canScrollLeft, setCanScrollLeft] = useState(false)
  const [canScrollRight, setCanScrollRight] = useState(false)

  const updateScrollAffordance = () => {
    const el = scrollRef.current
    if (!el) {
      setCanScrollLeft(false)
      setCanScrollRight(false)
      return
    }
    const max = el.scrollWidth - el.clientWidth
    setCanScrollLeft(el.scrollLeft > 1)
    setCanScrollRight(max > 1 && el.scrollLeft < max - 1)
  }

  useLayoutEffect(() => {
    const container = scrollRef.current
    const tabEl = activeTabElRef.current
    if (!container || !tabEl || activeTabId == null) return
    ensureChildFullyVisibleHorizontally(container, tabEl)
    updateScrollAffordance()
  }, [activeTabId, tabs.length])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    updateScrollAffordance()
    const onScroll = () => updateScrollAffordance()
    el.addEventListener('scroll', onScroll, { passive: true })
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => updateScrollAffordance()) : null
    ro?.observe(el)
    return () => {
      el.removeEventListener('scroll', onScroll)
      ro?.disconnect()
    }
  }, [tabs.length])

  const scrollByPage = (dir: -1 | 1) => {
    const el = scrollRef.current
    if (!el) return
    el.scrollBy({ left: dir * Math.max(120, el.clientWidth * 0.6), behavior: 'smooth' })
  }

  const tabContextMenu = (tabId: number) => {
    const idx = tabs.findIndex(t => t.id === tabId)
    const tab = idx >= 0 ? tabs[idx] : null
    const hasLeft = idx > 0
    const hasRight = idx >= 0 && idx < tabs.length - 1
    const hasOthers = tabs.length > 1
    return {
      items: [
        {
          key: 'reload',
          icon: <ReloadOutlined />,
          label: tabContentError[tabId] ? '重新加载脚本' : '重新加载',
          disabled: !tab || Boolean(tabContentLoading[tabId]),
          onClick: () => onReload(tabId),
        },
        { type: 'divider' as const },
        {
          key: 'close',
          label: '关闭',
          title: '关闭后下次进入数据开发将不再自动打开此标签',
          onClick: () => onClose(tabId),
        },
        {
          key: 'close-others',
          label: '关闭其他',
          disabled: !hasOthers,
          onClick: () => onCloseOthers(tabId),
        },
        {
          key: 'close-left',
          label: '关闭左侧',
          disabled: !hasLeft,
          onClick: () => onCloseLeft(tabId),
        },
        {
          key: 'close-right',
          label: '关闭右侧',
          disabled: !hasRight,
          onClick: () => onCloseRight(tabId),
        },
        { type: 'divider' as const },
        {
          key: 'close-all',
          label: '全部关闭',
          disabled: tabs.length === 0,
          onClick: () => onCloseAll(),
        },
        {
          key: 'session-hint',
          disabled: true,
          label: (
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              关闭即移出会话，下次不再自动恢复
            </span>
          ),
        },
      ],
    }
  }

  if (tabs.length === 0) {
    return (
      <span
        data-testid="studio-tabs-empty"
        style={{ padding: '0 16px', color: '#bbb', fontSize: 13 }}
      >
        双击左侧节点打开编辑
      </span>
    )
  }

  const overflowItems = tabs.map(tab => {
    const dirty = Boolean(versionDirtyMap[tab.id])
    const isActive = tab.id === activeTabId
    const contentPending = isEditorTabContentPending(tab)
    const contentLoading = Boolean(tabContentLoading[tab.id])
    const contentError = tabContentError[tab.id]
    return {
      key: String(tab.id),
      icon: isActive
        ? <CheckOutlined style={{ color: '#1677ff', fontSize: 12 }} />
        : <span style={{ display: 'inline-block', width: 12 }} />,
      label: (
        <span
          data-testid={`studio-tabs-overflow-item-${tab.id}`}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            maxWidth: 420,
            fontStyle: contentPending || contentLoading || contentError ? 'italic' : 'normal',
            color: contentError ? '#d48806' : undefined,
          }}
        >
          <Tag color={TYPE_COLOR[tab.node_type] || 'default'} style={{ margin: 0, fontSize: 11 }}>
            {tab.node_type}
          </Tag>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{tab.name}</span>
          {dirty && !contentPending && !contentError ? (
            <span style={{ color: '#faad14', fontSize: 10 }}>●</span>
          ) : null}
        </span>
      ),
      onClick: () => onActivate(tab.id),
    }
  })

  return (
    <div
      data-testid="studio-tabs-strip"
      style={{
        display: 'flex',
        alignItems: 'stretch',
        flex: 1,
        minWidth: 0,
        height: 40,
      }}
    >
      {(canScrollLeft || canScrollRight) && (
        <Button
          type="text"
          size="small"
          data-testid="studio-tabs-scroll-left"
          icon={<LeftOutlined style={{ fontSize: 11 }} />}
          disabled={!canScrollLeft}
          onClick={() => scrollByPage(-1)}
          style={{ ...SCROLL_BTN_STYLE, borderLeft: 'none', borderRight: '1px solid #f0f0f0' }}
        />
      )}
      <div
        ref={scrollRef}
        data-testid="studio-tabs-scroll"
        style={{
          display: 'flex',
          alignItems: 'stretch',
          flex: 1,
          minWidth: 0,
          overflowX: 'auto',
          overflowY: 'hidden',
          scrollbarWidth: 'thin',
        }}
      >
        {tabs.map(tab => {
          const dirty = Boolean(versionDirtyMap[tab.id])
          const isActive = tab.id === activeTabId
          const contentPending = isEditorTabContentPending(tab)
          const contentLoading = Boolean(tabContentLoading[tab.id])
          const contentError = tabContentError[tab.id]
          const kind = resolveStudioTabChrome({
            script_content: tab.script_content,
            loading: contentLoading,
            error: contentError,
          })
          const tabTitleColor = studioTabTitleColor(kind, isActive)
          return (
            <Dropdown key={tab.id} trigger={['contextMenu']} menu={tabContextMenu(tab.id)}>
              <Tooltip
                title={
                  contentLoading
                    ? '正在加载脚本…'
                    : contentError
                      ? `加载失败：${contentError}（再点一次或右键重新加载）`
                      : contentPending
                        ? '尚未加载正文，点击后拉取（会话恢复的后台标签）'
                        : tab.name
                }
              >
                <div
                  ref={isActive ? activeTabElRef : undefined}
                  data-testid={`studio-tab-${tab.id}`}
                  data-chrome={kind}
                  data-active={isActive ? 'true' : undefined}
                  onClick={() => onActivate(tab.id)}
                  onAuxClick={e => {
                    if (e.button === 1) {
                      e.preventDefault()
                      e.stopPropagation()
                      onClose(tab.id)
                    }
                  }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '0 14px', height: 40, cursor: 'pointer', whiteSpace: 'nowrap',
                    borderRight: '1px solid #f0f0f0',
                    borderBottom: isActive ? '2px solid #1677ff' : '2px solid transparent',
                    background: isActive ? '#fff' : '#fafafa',
                    color: tabTitleColor,
                    fontSize: 13,
                    flexShrink: 0,
                  }}
                >
                  <Tag
                    color={TYPE_COLOR[tab.node_type] || 'default'}
                    style={{
                      margin: 0,
                      fontSize: 11,
                      opacity: (contentPending || contentError) && !contentLoading ? 0.65 : 1,
                    }}
                  >
                    {tab.node_type}
                  </Tag>
                  <span
                    data-testid={`studio-tab-title-${tab.id}`}
                    style={{
                      fontStyle: studioTabTitleItalic(kind) ? 'italic' : 'normal',
                      // 固定标题槽，避免斜体/截断切换时挤压右侧 ● / 关闭钮
                      width: 148,
                      maxWidth: 148,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      flexShrink: 0,
                    }}
                  >
                    {tab.name}
                  </span>
                  <TabStatusSlot
                    tabId={tab.id}
                    loading={contentLoading}
                    error={!contentLoading ? contentError : undefined}
                    dirty={dirty && !contentPending && !contentError}
                  />
                  <CloseCircleOutlined
                    data-testid={`studio-tab-close-${tab.id}`}
                    style={{ fontSize: 12, color: '#999', marginLeft: 2, flexShrink: 0 }}
                    onClick={e => { e.stopPropagation(); onClose(tab.id) }}
                  />
                </div>
              </Tooltip>
            </Dropdown>
          )
        })}
      </div>
      {(canScrollLeft || canScrollRight) && (
        <Button
          type="text"
          size="small"
          data-testid="studio-tabs-scroll-right"
          icon={<RightOutlined style={{ fontSize: 11 }} />}
          disabled={!canScrollRight}
          onClick={() => scrollByPage(1)}
          style={SCROLL_BTN_STYLE}
        />
      )}
      <Dropdown
        trigger={['click']}
        placement="bottomRight"
        menu={{ items: overflowItems, style: { maxHeight: 360, overflowY: 'auto' } }}
      >
        <Tooltip title="打开的文件">
          <Button
            type="text"
            size="small"
            data-testid="studio-tabs-overflow"
            icon={<DownOutlined style={{ fontSize: 11 }} />}
            style={SCROLL_BTN_STYLE}
            aria-label="打开的文件列表"
          />
        </Tooltip>
      </Dropdown>
    </div>
  )
}
