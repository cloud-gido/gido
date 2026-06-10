/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useMemo, type ReactNode } from 'react'
import { Layout, Menu, type MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  ApiOutlined, AppstoreOutlined, DashboardOutlined, GatewayOutlined,
  LineChartOutlined, DatabaseOutlined, AuditOutlined,
} from '@ant-design/icons'
import ProductBrandBlock from './ProductBrandBlock'
import WorkspaceHeaderBar from './shell/WorkspaceHeaderBar'
import WorkspaceShellModals from './shell/WorkspaceShellModals'
import { useWorkspaceShell } from './shell/useWorkspaceShell'
import { R } from '../routes'
import { canSeeServiceMenu } from '../serviceMenuPolicy'
import { P } from '../perm'
import { ServiceDataProvider } from '../pages/service/ServiceContext'

const { Sider, Content, Header } = Layout

const MENU_DEF: { key: string; icon: ReactNode; label: string; perm: string }[] = [
  { key: R.service.overview, icon: <DashboardOutlined />, label: '服务概览', perm: P.GIDO_SERVICE_READ },
  { key: R.service.apis, icon: <ApiOutlined />, label: 'API 开发', perm: P.GIDO_SERVICE_READ },
  { key: R.service.approval, icon: <AuditOutlined />, label: '发布审批', perm: P.GIDO_SERVICE_READ },
  { key: R.service.apps, icon: <AppstoreOutlined />, label: '应用管理', perm: P.GIDO_SERVICE_READ },
  { key: R.service.monitor, icon: <LineChartOutlined />, label: '调用监控', perm: P.GIDO_SERVICE_READ },
  { key: R.service.gateway, icon: <GatewayOutlined />, label: '开放网关', perm: P.GIDO_SERVICE_READ },
]

const CONFIG_DEF: { key: string; icon: React.ReactNode; label: string; perm: string }[] = [
  { key: R.service.datasource, icon: <DatabaseOutlined />, label: '数据源', perm: P.GIDO_BATCH_DATASOURCE_READ },
]

export default function ServiceLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const shell = useWorkspaceShell()

  const menuItems = useMemo((): MenuProps['items'] => {
    const main = MENU_DEF.filter(m => canSeeServiceMenu(shell.user, shell.currentWorkspace, m.key, m.perm))
      .map(m => ({ key: m.key, icon: m.icon, label: m.label }))
    const config = CONFIG_DEF.filter(m => canSeeServiceMenu(shell.user, shell.currentWorkspace, m.key, m.perm))
      .map(m => ({ key: m.key, icon: m.icon, label: m.label }))
    const out: MenuProps['items'] = [...main]
    if (config.length) {
      out.push({ type: 'divider' as const })
      out.push({
        type: 'group',
        label: '平台配置',
        children: config,
      })
    }
    return out
  }, [shell.user, shell.currentWorkspace])

  return (
    <ServiceDataProvider>
      <Layout className="dw-app-shell dw-app-shell--service" style={{ minHeight: '100vh', background: 'var(--dw-bg)' }}>
        <Sider theme="dark" width={216} className="dw-menu-dark dw-sider-unified dw-accent-service">
          <div className="dw-sider-brand dw-accent-service">
            <ProductBrandBlock variant="service" />
            <div className="dw-accent-bar" aria-hidden />
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[location.pathname]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: 'none', paddingTop: 8, paddingBottom: 16, background: 'transparent' }}
          />
        </Sider>
        <Layout style={{ background: 'transparent' }}>
          <Header className="dw-header-bar" style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
            <WorkspaceHeaderBar
              product="service"
              user={shell.user}
              currentWorkspace={shell.currentWorkspace}
              workspaces={shell.workspaces}
              wsLabel={shell.wsLabel}
              setCurrentWorkspace={shell.setCurrentWorkspace}
              openTzModal={shell.openTzModal}
              onCreateWorkspace={() => shell.setCreateWsOpen(true)}
              showWorkspaceSettings
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
    </ServiceDataProvider>
  )
}
