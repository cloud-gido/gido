/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 * Stream 侧栏：对标实时计算开发控制台 — 作业开发 / 资源管理 / 作业运维 / 发布审批。
 */
import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu } from 'antd'
import type { MenuProps } from 'antd'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  CodeOutlined, MonitorOutlined, AuditOutlined, InboxOutlined,
  AppstoreOutlined,
} from '@ant-design/icons'
import ProductBrandBlock from './ProductBrandBlock'
import WorkspaceHeaderBar from './shell/WorkspaceHeaderBar'
import WorkspaceShellModals from './shell/WorkspaceShellModals'
import { useWorkspaceShell } from './shell/useWorkspaceShell'
import { R } from '../routes'

const { Sider, Content, Header } = Layout

const RESOURCES_GROUP_KEY = 'stream-resources'

const MENU_ITEMS: MenuProps['items'] = [
  { key: R.stream.studio, icon: <CodeOutlined />, label: '作业开发' },
  {
    key: RESOURCES_GROUP_KEY,
    icon: <AppstoreOutlined />,
    label: '资源管理',
    children: [
      { key: R.stream.resources, icon: <AppstoreOutlined />, label: '总览' },
      { key: R.stream.resourcesJars, icon: <InboxOutlined />, label: 'JAR 包' },
    ],
  },
  { key: R.stream.monitor, icon: <MonitorOutlined />, label: '作业运维' },
  { key: R.stream.approval, icon: <AuditOutlined />, label: '发布审批' },
]

function selectedStreamKey(pathname: string): string {
  if (pathname === R.stream.overview) return R.stream.monitor
  if (pathname === R.stream.jars || pathname.startsWith(`${R.stream.resources}/`)) {
    if (pathname.includes('/jars')) return R.stream.resourcesJars
    return R.stream.resources
  }
  if (pathname === R.stream.resources) return R.stream.resources
  return pathname
}

export default function StreamLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const shell = useWorkspaceShell()
  const selectedKey = selectedStreamKey(location.pathname)
  const underResources = selectedKey === R.stream.resources || selectedKey === R.stream.resourcesJars

  const [openKeys, setOpenKeys] = useState<string[]>(() => (underResources ? [RESOURCES_GROUP_KEY] : []))

  useEffect(() => {
    if (underResources) {
      setOpenKeys(prev => (prev.includes(RESOURCES_GROUP_KEY) ? prev : [...prev, RESOURCES_GROUP_KEY]))
    }
  }, [underResources])

  const selectedKeys = useMemo(() => [selectedKey], [selectedKey])

  return (
    <Layout className="dw-app-shell dw-app-shell--stream" style={{ minHeight: '100vh', background: 'var(--dw-bg)' }}>
      <Sider
        theme="dark"
        width={216}
        className="dw-menu-dark dw-sider-unified dw-accent-stream"
        style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}
      >
        <div className="dw-sider-brand dw-accent-stream">
          <ProductBrandBlock variant="stream" />
          <div className="dw-accent-bar" aria-hidden />
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          openKeys={openKeys}
          onOpenChange={keys => setOpenKeys(keys as string[])}
          items={MENU_ITEMS}
          onClick={({ key }) => {
            if (key === RESOURCES_GROUP_KEY) {
              navigate(R.stream.resources)
              return
            }
            navigate(key)
          }}
          style={{ borderInlineEnd: 'none', paddingTop: 8, paddingBottom: 16, background: 'transparent' }}
        />
      </Sider>
      <Layout style={{ background: 'transparent' }}>
        <Header className="dw-header-bar" style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
          <WorkspaceHeaderBar
            product="stream"
            user={shell.user}
            currentWorkspace={shell.currentWorkspace}
            workspaces={shell.workspaces}
            wsLabel={shell.wsLabel}
            setCurrentWorkspace={shell.setCurrentWorkspace}
            openTzModal={shell.openTzModal}
            onCreateWorkspace={() => shell.setCreateWsOpen(true)}
          />
        </Header>
        <Content className="dw-content-wrap">
          <Outlet />
        </Content>
      </Layout>

      <WorkspaceShellModals
        tzModal={shell.tzModal}
        setTzModal={shell.setTzModal}
        tzForm={shell.tzForm}
        handleSaveTz={shell.handleSaveTz}
        createWsOpen={shell.createWsOpen}
        setCreateWsOpen={shell.setCreateWsOpen}
        wsForm={shell.wsForm}
        submitNewWorkspace={shell.submitNewWorkspace}
      />
    </Layout>
  )
}
