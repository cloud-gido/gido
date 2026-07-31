/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 * Stream 侧栏：作业开发 / 作业运维 / 资源管理 / 发布审批。
 */
import { useEffect, useMemo, useState } from 'react'
import { Layout, Menu } from 'antd'
import type { MenuProps } from 'antd'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  CodeOutlined, MonitorOutlined, AuditOutlined, InboxOutlined,
  AppstoreOutlined, ApiOutlined, FileZipOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import ProductBrandBlock from './ProductBrandBlock'
import WorkspaceHeaderBar from './shell/WorkspaceHeaderBar'
import WorkspaceShellModals from './shell/WorkspaceShellModals'
import { useWorkspaceShell } from './shell/useWorkspaceShell'
import { R } from '../routes'

const { Sider, Content, Header } = Layout

const RESOURCE_GROUP_KEY = 'stream-resources'

const MENU_ITEMS: MenuProps['items'] = [
  { key: R.stream.studio, icon: <CodeOutlined />, label: '作业开发' },
  { key: R.stream.pipelines, icon: <ApartmentOutlined />, label: '数据管道' },
  { key: R.stream.monitor, icon: <MonitorOutlined />, label: '作业运维' },
  {
    key: RESOURCE_GROUP_KEY,
    icon: <AppstoreOutlined />,
    label: '资源管理',
    children: [
      { key: R.stream.resources, icon: <AppstoreOutlined />, label: '资源总览' },
      { key: R.stream.resourcesJars, icon: <InboxOutlined />, label: 'JAR 包' },
      { key: R.stream.resourcesConnectors, icon: <ApiOutlined />, label: '连接器' },
      { key: R.stream.resourcesFiles, icon: <FileZipOutlined />, label: '依赖文件' },
    ],
  },
  { key: R.stream.approval, icon: <AuditOutlined />, label: '发布审批' },
]

function selectedStreamKey(pathname: string): string {
  if (pathname === R.stream.overview) return R.stream.monitor
  if (pathname === R.stream.jars || pathname.startsWith(`${R.stream.resources}/`)) {
    if (pathname.includes('/jars')) return R.stream.resourcesJars
    if (pathname.includes('/connectors')) return R.stream.resourcesConnectors
    if (pathname.includes('/files')) return R.stream.resourcesFiles
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
  const underResources = selectedKey === R.stream.resources
    || selectedKey === R.stream.resourcesJars
    || selectedKey === R.stream.resourcesConnectors
    || selectedKey === R.stream.resourcesFiles

  const [openKeys, setOpenKeys] = useState<string[]>(() => (underResources ? [RESOURCE_GROUP_KEY] : []))

  useEffect(() => {
    if (underResources) {
      setOpenKeys(prev => (prev.includes(RESOURCE_GROUP_KEY) ? prev : [...prev, RESOURCE_GROUP_KEY]))
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
          onClick={({ key }) => navigate(key)}
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
