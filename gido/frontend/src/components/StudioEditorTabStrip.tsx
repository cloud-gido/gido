/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据开发编辑器 Tab 条（会话壳 / 懒加载铬态），供 Studio 挂载与 E2E 覆盖。
 */
import { Dropdown, Tag, Tooltip } from 'antd'
import {
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { isEditorTabContentPending } from '../utils/editorSessionStore'
import {
  resolveStudioTabChrome,
  studioTabTitleColor,
  studioTabTitleItalic,
} from '../utils/studioTabChrome'

const TYPE_COLOR: Record<string, string> = {
  SQL: 'blue', PYTHON: 'green', SHELL: 'orange', SYNC: 'purple', VIRTUAL: 'default', DEPENDENT: 'magenta',
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
  dirtyMap: Record<number, string | undefined>
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

export default function StudioEditorTabStrip({
  tabs,
  activeTabId,
  dirtyMap,
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

  return (
    <>
      {tabs.map(tab => {
        const dirty = dirtyMap[tab.id] !== undefined
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
                      : undefined
              }
            >
              <div
                data-testid={`studio-tab-${tab.id}`}
                data-chrome={kind}
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
                    maxWidth: 180,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {tab.name}
                </span>
                {contentLoading && (
                  <LoadingOutlined data-testid={`studio-tab-loading-${tab.id}`} style={{ fontSize: 11, color: '#1677ff' }} />
                )}
                {contentError && !contentLoading && (
                  <ExclamationCircleOutlined
                    data-testid={`studio-tab-error-${tab.id}`}
                    style={{ fontSize: 11, color: '#d48806' }}
                  />
                )}
                {dirty && !contentPending && !contentError && (
                  <span style={{ color: '#faad14', fontSize: 10 }}>●</span>
                )}
                <CloseCircleOutlined
                  data-testid={`studio-tab-close-${tab.id}`}
                  style={{ fontSize: 12, color: '#999', marginLeft: 2 }}
                  onClick={e => { e.stopPropagation(); onClose(tab.id) }}
                />
              </div>
            </Tooltip>
          </Dropdown>
        )
      })}
    </>
  )
}
