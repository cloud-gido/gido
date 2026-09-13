/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { Spin } from 'antd'
import LoginPage from './pages/Login'
import AboutPage from './pages/About'
import ProductWorkspaceShell from './components/shell/ProductWorkspaceShell'
import { ServiceProductOutlet } from './components/shell/ServiceProductSider'
import ShellThemeProvider from './components/ShellThemeProvider'
import { R } from './routes'
import RequireGidoBatchRoute from './components/RequireGidoBatchRoute'
import RequireStreamRoute from './components/RequireStreamRoute'
import RequireServiceRoute from './components/RequireServiceRoute'
import { useAppStore } from './store'
import { defaultBatchHome } from './workspaceMenuPolicy'
import { defaultServiceHome } from './serviceMenuPolicy'

const StudioPage = lazy(() => import('./pages/Studio'))
const DataMapPage = lazy(() => import('./pages/DataMap'))
const ProbePage = lazy(() => import('./pages/Probe'))
const QualityPage = lazy(() => import('./pages/Quality'))
const IntegrationPage = lazy(() => import('./pages/Integration'))
const OperationPage = lazy(() => import('./pages/Operation'))
const RunHistoryPage = lazy(() => import('./pages/RunHistory'))
const RunHistoryDetailPage = lazy(() => import('./pages/RunHistoryDetail'))
const AlertCenterPage = lazy(() => import('./pages/AlertCenter'))
const ApprovalPage = lazy(() => import('./pages/Approval'))
const DatasourcePage = lazy(() => import('./pages/Datasource'))
const WorkflowPage = lazy(() => import('./pages/Workflow'))
const StreamStudioPage = lazy(() => import('./pages/StreamStudio'))
const StreamPipelinePage = lazy(() => import('./pages/StreamPipeline'))
const StreamResourcesPage = lazy(() => import('./pages/StreamResources'))
const StreamJarLibraryPage = lazy(() => import('./pages/StreamJarLibrary'))
const StreamConnectorLibraryPage = lazy(() => import('./pages/StreamConnectorLibrary'))
const StreamFileLibraryPage = lazy(() => import('./pages/StreamFileLibrary'))
const StreamMonitorPage = lazy(() => import('./pages/StreamMonitor'))
const SystemRbacPage = lazy(() => import('./pages/SystemRbac'))
const WorkspaceSettingsPage = lazy(() => import('./pages/WorkspaceSettings'))
const ServiceOverviewPage = lazy(() => import('./pages/service/ServiceOverviewPage'))
const ServiceApisPage = lazy(() => import('./pages/service/ServiceApisPage'))
const ServiceAppsPage = lazy(() => import('./pages/service/ServiceAppsPage'))
const ServiceMonitorPage = lazy(() => import('./pages/service/ServiceMonitorPage'))
const ServiceGatewayPage = lazy(() => import('./pages/service/ServiceGatewayPage'))

function RouteFallback() {
  return (
    <div style={{ padding: 48, textAlign: 'center' }}>
      <Spin />
    </div>
  )
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('token')
  return token ? children : <Navigate to={R.login} replace />
}

function RootRedirect() {
  const token = localStorage.getItem('token')
  const { user, currentWorkspace } = useAppStore()
  return token
    ? <Navigate to={defaultBatchHome(user, currentWorkspace)} replace />
    : <Navigate to={R.login} replace />
}

function BatchIndexRedirect() {
  const { user, currentWorkspace } = useAppStore()
  return <Navigate to={defaultBatchHome(user, currentWorkspace)} replace />
}

function ServiceIndexRedirect() {
  const { user, currentWorkspace } = useAppStore()
  return <Navigate to={defaultServiceHome(user, currentWorkspace)} replace />
}

/** 流产品段：守卫通过后渲染嵌套子路由 */
function StreamSegment() {
  return (
    <RequireStreamRoute>
      <Outlet />
    </RequireStreamRoute>
  )
}

export default function App() {
  return (
    <ShellThemeProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path={R.login} element={<LoginPage />} />
            <Route path={R.about} element={<AboutPage />} />

            {/* 统一工作台壳：/gido/* 下切批/流/服不卸载空间顶栏 */}
            <Route path="/gido" element={<RequireAuth><ProductWorkspaceShell /></RequireAuth>}>
              <Route path="batch">
                <Route index element={<BatchIndexRedirect />} />
                <Route path="studio" element={<RequireGidoBatchRoute><StudioPage /></RequireGidoBatchRoute>} />
                <Route path="workflow" element={<RequireGidoBatchRoute><WorkflowPage /></RequireGidoBatchRoute>} />
                <Route path="datamap" element={<RequireGidoBatchRoute><DataMapPage /></RequireGidoBatchRoute>} />
                <Route path="probe" element={<RequireGidoBatchRoute><ProbePage /></RequireGidoBatchRoute>} />
                <Route path="quality" element={<RequireGidoBatchRoute><QualityPage /></RequireGidoBatchRoute>} />
                <Route path="integration" element={<RequireGidoBatchRoute><IntegrationPage /></RequireGidoBatchRoute>} />
                <Route path="run-history" element={<RequireGidoBatchRoute><RunHistoryPage /></RequireGidoBatchRoute>} />
                <Route path="run-history/:id" element={<RequireGidoBatchRoute><RunHistoryDetailPage /></RequireGidoBatchRoute>} />
                <Route path="operation" element={<RequireGidoBatchRoute><OperationPage /></RequireGidoBatchRoute>} />
                <Route path="alert" element={<RequireGidoBatchRoute><AlertCenterPage /></RequireGidoBatchRoute>} />
                <Route path="approval" element={<RequireGidoBatchRoute><ApprovalPage /></RequireGidoBatchRoute>} />
                <Route path="dataservice" element={<Navigate to={R.service.apis} replace />} />
                <Route path="datasource" element={<RequireGidoBatchRoute><DatasourcePage /></RequireGidoBatchRoute>} />
                <Route path="workspace-settings" element={<RequireGidoBatchRoute><WorkspaceSettingsPage /></RequireGidoBatchRoute>} />
                <Route path="admin" element={<RequireGidoBatchRoute><SystemRbacPage /></RequireGidoBatchRoute>} />
                <Route path="system/integration" element={<RequireGidoBatchRoute><SystemRbacPage view="integration" /></RequireGidoBatchRoute>} />
              </Route>

              <Route path="stream" element={<StreamSegment />}>
                <Route index element={<Navigate to={R.stream.studio} replace />} />
                <Route path="studio" element={<StreamStudioPage />} />
                <Route path="pipelines" element={<StreamPipelinePage />} />
                <Route path="resources" element={<StreamResourcesPage />} />
                <Route path="resources/jars" element={<StreamJarLibraryPage />} />
                <Route path="resources/connectors" element={<StreamConnectorLibraryPage />} />
                <Route path="resources/files" element={<StreamFileLibraryPage />} />
                <Route path="jars" element={<Navigate to={R.stream.resourcesJars} replace />} />
                <Route path="monitor" element={<StreamMonitorPage />} />
                <Route path="overview" element={<Navigate to={R.stream.monitor} replace />} />
                <Route path="flink-sessions" element={<Navigate to={R.stream.monitor} replace />} />
                <Route path="approval" element={<ApprovalPage />} />
              </Route>

              <Route path="service" element={<ServiceProductOutlet />}>
                <Route index element={<ServiceIndexRedirect />} />
                <Route path="overview" element={<RequireServiceRoute><ServiceOverviewPage /></RequireServiceRoute>} />
                <Route path="apis" element={<RequireServiceRoute><ServiceApisPage /></RequireServiceRoute>} />
                <Route path="apps" element={<RequireServiceRoute><ServiceAppsPage /></RequireServiceRoute>} />
                <Route path="monitor" element={<RequireServiceRoute><ServiceMonitorPage /></RequireServiceRoute>} />
                <Route path="gateway" element={<RequireServiceRoute><ServiceGatewayPage /></RequireServiceRoute>} />
                <Route path="datasource" element={<RequireServiceRoute><DatasourcePage /></RequireServiceRoute>} />
                <Route path="approval" element={<RequireServiceRoute><ApprovalPage /></RequireServiceRoute>} />
              </Route>
            </Route>

            <Route path="/" element={<RootRedirect />} />
            <Route path="*" element={<RootRedirect />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ShellThemeProvider>
  )
}
