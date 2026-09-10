/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useMemo, type ReactNode } from 'react'
import { Layout, Menu, type MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  ApiOutlined, AppstoreOutlined, DashboardOutlined, GatewayOutlined,
  LineChartOutlined, DatabaseOutlined, AuditOutlined,
} from '@ant-design/icons'
import ProductBrandBlock from '../ProductBrandBlock'
import { R } from '../../routes'
import { canSeeServiceMenu } from '../../serviceMenuPolicy'
import { P } from '../../perm'
import { ServiceDataProvider } from '../../pages/service/ServiceContext'

const { Sider } = Layout

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

type Props = {
  user: any
  currentWorkspace: any
}

export default function ServiceProductSider({ user, currentWorkspace }: Props) {
  const navigate = useNavigate()
  const location = useLocation()

  const menuItems = useMemo((): MenuProps['items'] => {
    const main = MENU_DEF.filter(m => canSeeServiceMenu(user, currentWorkspace, m.key, m.perm))
      .map(m => ({ key: m.key, icon: m.icon, label: m.label }))
    const config = CONFIG_DEF.filter(m => canSeeServiceMenu(user, currentWorkspace, m.key, m.perm))
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
  }, [user, currentWorkspace])

  return (
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
  )
}

/** 服务产品数据上下文：仅包住 /gido/service/*，离开子产品即卸载 */
export function ServiceProductOutlet() {
  return (
    <ServiceDataProvider>
      <Outlet />
    </ServiceDataProvider>
  )
}
