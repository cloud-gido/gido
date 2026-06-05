/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Layout, Menu } from 'antd'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  CodeOutlined, ThunderboltOutlined, MonitorOutlined, CloudServerOutlined, ApiOutlined, AuditOutlined,
} from '@ant-design/icons'
import ProductBrandBlock from './ProductBrandBlock'
import WorkspaceHeaderBar from './shell/WorkspaceHeaderBar'
import WorkspaceShellModals from './shell/WorkspaceShellModals'
import { useWorkspaceShell } from './shell/useWorkspaceShell'
import { R } from '../routes'

const { Sider, Content, Header } = Layout

const MENU_ITEMS = [
  { key: R.stream.studio, icon: <CodeOutlined />, label: '作业开发' },
  { key: R.stream.monitor, icon: <MonitorOutlined />, label: '作业运维' },
  { key: R.stream.approval, icon: <AuditOutlined />, label: '发布审批' },
  { key: R.stream.flinkSessions, icon: <ApiOutlined />, label: 'Flink 集群连接' },
  { key: R.stream.overview, icon: <CloudServerOutlined />, label: '集群与健康' },
]

export default function StreamLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const shell = useWorkspaceShell()

  return (
    <Layout className="dw-app-shell dw-app-shell--stream" style={{ minHeight: '100vh', background: 'var(--dw-bg)' }}>
      <Sider theme="dark" width={216} className="dw-menu-dark dw-sider-unified dw-accent-stream">
        <div className="dw-sider-brand dw-accent-stream">
          <ProductBrandBlock variant="stream" />
          <div className="dw-accent-bar" aria-hidden />
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
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
