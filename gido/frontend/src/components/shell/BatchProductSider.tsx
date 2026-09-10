/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Layout, Menu, type MenuProps } from 'antd'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  CodeOutlined, DatabaseOutlined, ApartmentOutlined, SafetyOutlined,
  MonitorOutlined, ApiOutlined, SwapOutlined, BellOutlined,
  SettingOutlined, TeamOutlined, DeploymentUnitOutlined,
  ExperimentOutlined, PartitionOutlined, AuditOutlined, HistoryOutlined,
} from '@ant-design/icons'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import ProductBrandBlock from '../ProductBrandBlock'
import { R } from '../../routes'
import { canSeeBatchMenu } from '../../workspaceMenuPolicy'
import { isPlatformAdmin, P } from '../../perm'

const { Sider } = Layout

const SYSTEM_MENU_GROUP_KEY = 'dw-system-menu'

type MenuItemDef = { key: string; icon: ReactNode; label: string; perm: string | string[] }

const MENU_GROUPS: { label: string; items: MenuItemDef[] }[] = [
  {
    label: '开发生产',
    items: [
      { key: R.batch.studio, icon: <CodeOutlined />, label: '数据开发', perm: P.GIDO_BATCH_STUDIO_READ },
      { key: R.batch.workflow, icon: <ApartmentOutlined />, label: '工作流', perm: P.GIDO_BATCH_WORKFLOW_READ },
      { key: R.batch.integration, icon: <SwapOutlined />, label: '数据集成', perm: P.GIDO_BATCH_INTEGRATION_READ },
      { key: R.batch.runHistory, icon: <HistoryOutlined />, label: '运行历史', perm: [P.GIDO_BATCH_STUDIO_READ, P.GIDO_BATCH_PROBE_READ] },
      { key: R.batch.operation, icon: <MonitorOutlined />, label: '实例中心', perm: P.GIDO_BATCH_OPERATION_READ },
      { key: R.batch.alert, icon: <BellOutlined />, label: '告警中心', perm: P.GIDO_BATCH_OPERATION_READ },
      { key: R.batch.approval, icon: <AuditOutlined />, label: '发布审批', perm: P.GIDO_BATCH_OPERATION_READ },
    ],
  },
  {
    label: '数据治理',
    items: [
      { key: R.batch.datamap, icon: <DatabaseOutlined />, label: '数据字典', perm: P.GIDO_BATCH_DATAMAP_READ },
      { key: R.batch.probe, icon: <ExperimentOutlined />, label: '数据探查', perm: P.GIDO_BATCH_PROBE_READ },
      { key: R.batch.quality, icon: <SafetyOutlined />, label: '数据质量', perm: P.GIDO_BATCH_QUALITY_READ },
    ],
  },
  {
    label: '平台配置',
    items: [
      { key: R.batch.datasource, icon: <ApiOutlined />, label: '数据源', perm: P.GIDO_BATCH_DATASOURCE_READ },
      { key: R.batch.workspaceSettings, icon: <PartitionOutlined />, label: '空间设置', perm: P.GIDO_BATCH_DATASOURCE_READ },
    ],
  },
]

type Props = {
  user: any
  currentWorkspace: any
}

export default function BatchProductSider({ user, currentWorkspace }: Props) {
  const navigate = useNavigate()
  const location = useLocation()
  const [systemMenuOpenKeys, setSystemMenuOpenKeys] = useState<string[]>([])

  useEffect(() => {
    const p = location.pathname
    if (p === R.batch.admin || p === R.batch.systemIntegration) {
      setSystemMenuOpenKeys([SYSTEM_MENU_GROUP_KEY])
    } else {
      setSystemMenuOpenKeys([])
    }
  }, [location.pathname])

  const menuItems = useMemo((): MenuProps['items'] => {
    const out: MenuProps['items'] = []
    for (const group of MENU_GROUPS) {
      const children = group.items
        .filter(m => canSeeBatchMenu(user, currentWorkspace, m.key, m.perm))
        .map(m => ({ key: m.key, icon: m.icon, label: m.label }))
      if (children.length) {
        out.push({ type: 'group', label: group.label, children })
      }
    }
    if (isPlatformAdmin(user)) {
      out.push({
        key: SYSTEM_MENU_GROUP_KEY,
        icon: <SettingOutlined />,
        label: '系统管理',
        children: [
          { key: R.batch.admin, icon: <TeamOutlined />, label: '成员与权限' },
          { key: R.batch.systemIntegration, icon: <DeploymentUnitOutlined />, label: '平台集成' },
        ],
      })
    }
    return out
  }, [user, currentWorkspace])

  return (
    <Sider theme="dark" width={216} className="dw-menu-dark dw-sider-unified dw-accent-batch">
      <div className="dw-sider-brand dw-accent-batch">
        <ProductBrandBlock variant="batch" />
        <div className="dw-accent-bar" aria-hidden />
      </div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[
          location.pathname.startsWith(R.batch.runHistory)
            ? R.batch.runHistory
            : location.pathname,
        ]}
        openKeys={systemMenuOpenKeys}
        onOpenChange={keys => setSystemMenuOpenKeys(keys as string[])}
        items={menuItems}
        onClick={({ key }) => {
          if (key === SYSTEM_MENU_GROUP_KEY) return
          navigate(key)
        }}
        style={{ borderInlineEnd: 'none', paddingTop: 8, paddingBottom: 16 }}
      />
    </Sider>
  )
}
