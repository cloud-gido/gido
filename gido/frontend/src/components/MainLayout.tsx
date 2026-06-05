/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Layout, Menu, type MenuProps } from 'antd'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  CodeOutlined, DatabaseOutlined, ApartmentOutlined, SafetyOutlined,
  MonitorOutlined, ApiOutlined, SwapOutlined,
  SettingOutlined, FolderAddOutlined, TeamOutlined, DeploymentUnitOutlined,
  ExperimentOutlined, PartitionOutlined, AuditOutlined,
} from '@ant-design/icons'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import ProductBrandBlock from './ProductBrandBlock'
import WorkspaceHeaderBar from './shell/WorkspaceHeaderBar'
import WorkspaceShellModals from './shell/WorkspaceShellModals'
import { useWorkspaceShell } from './shell/useWorkspaceShell'
import { R } from '../routes'
import { canSeeBatchMenu } from '../workspaceMenuPolicy'
import { isPlatformAdmin, P } from '../perm'

const { Sider, Content, Header } = Layout

const SYSTEM_MENU_GROUP_KEY = 'dw-system-menu'

type MenuItemDef = { key: string; icon: ReactNode; label: string; perm: string | string[] }

const MENU_GROUPS: { label: string; items: MenuItemDef[] }[] = [
  {
    label: '开发生产',
    items: [
      { key: R.batch.studio, icon: <CodeOutlined />, label: '数据开发', perm: P.GIDO_BATCH_STUDIO_READ },
      { key: R.batch.workflow, icon: <ApartmentOutlined />, label: '工作流', perm: P.GIDO_BATCH_WORKFLOW_READ },
      { key: R.batch.integration, icon: <SwapOutlined />, label: '数据集成', perm: P.GIDO_BATCH_INTEGRATION_READ },
      { key: R.batch.operation, icon: <MonitorOutlined />, label: '运维中心', perm: P.GIDO_BATCH_OPERATION_READ },
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

export default function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const shell = useWorkspaceShell()
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
        .filter(m => canSeeBatchMenu(shell.user, shell.currentWorkspace, m.key, m.perm))
        .map(m => ({ key: m.key, icon: m.icon, label: m.label }))
      if (children.length) {
        out.push({ type: 'group', label: group.label, children })
      }
    }
    if (isPlatformAdmin(shell.user)) {
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
  }, [shell.user, shell.currentWorkspace])

  return (
    <Layout className="dw-app-shell dw-app-shell--batch" style={{ minHeight: '100vh', background: 'var(--dw-bg)' }}>
      <Sider theme="dark" width={216} className="dw-menu-dark dw-sider-unified dw-accent-batch">
        <div className="dw-sider-brand dw-accent-batch">
          <ProductBrandBlock variant="batch" />
          <div className="dw-accent-bar" aria-hidden />
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
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
      <Layout style={{ background: 'transparent' }}>
        <Header className="dw-header-bar" style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
          <WorkspaceHeaderBar
            product="batch"
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
        tzHint="影响节点运行时 $[...] 日期表达式基准"
      />
    </Layout>
  )
}
