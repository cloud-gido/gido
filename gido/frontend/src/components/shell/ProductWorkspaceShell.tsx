/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 租户工作台壳：空间上下文与顶栏跨批/流/服保持挂载；切子产品只换侧栏与内容 Outlet。
 */
import { Layout } from 'antd'
import { Outlet, useLocation } from 'react-router-dom'
import WorkspaceHeaderBar from './WorkspaceHeaderBar'
import WorkspaceShellModals from './WorkspaceShellModals'
import { useWorkspaceShell } from './useWorkspaceShell'
import { productFromPath } from './productFromPath'
import BatchProductSider from './BatchProductSider'
import StreamProductSider from './StreamProductSider'
import ServiceProductSider from './ServiceProductSider'

const { Content, Header } = Layout

export default function ProductWorkspaceShell() {
  const location = useLocation()
  const product = productFromPath(location.pathname)
  const shell = useWorkspaceShell()

  return (
    <Layout
      className={`dw-app-shell dw-app-shell--${product}`}
      style={{ minHeight: '100vh', background: 'var(--dw-bg)' }}
    >
      {product === 'batch' && (
        <BatchProductSider user={shell.user} currentWorkspace={shell.currentWorkspace} />
      )}
      {product === 'stream' && <StreamProductSider />}
      {product === 'service' && (
        <ServiceProductSider user={shell.user} currentWorkspace={shell.currentWorkspace} />
      )}

      <Layout style={{ background: 'transparent' }}>
        <Header className="dw-header-bar" style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
          <WorkspaceHeaderBar
            product={product}
            user={shell.user}
            currentWorkspace={shell.currentWorkspace}
            workspaces={shell.workspaces}
            wsLabel={shell.wsLabel}
            setCurrentWorkspace={shell.setCurrentWorkspace}
            openTzModal={shell.openTzModal}
            onCreateWorkspace={() => shell.setCreateWsOpen(true)}
            showWorkspaceSettings={product === 'batch' || product === 'service'}
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
        tzHint={product === 'batch' ? '影响节点运行时 $[...] 日期表达式基准' : undefined}
      />
    </Layout>
  )
}
