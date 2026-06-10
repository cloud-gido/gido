/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import LoginPage from './pages/Login'
import AboutPage from './pages/About'
import MainLayout from './components/MainLayout'
import StreamLayout from './components/StreamLayout'
import ServiceLayout from './components/ServiceLayout'
import ShellThemeProvider from './components/ShellThemeProvider'
import StudioPage from './pages/Studio'
import DataMapPage from './pages/DataMap'
import ProbePage from './pages/Probe'
import QualityPage from './pages/Quality'
import IntegrationPage from './pages/Integration'
import OperationPage from './pages/Operation'
import ApprovalPage from './pages/Approval'
import DatasourcePage from './pages/Datasource'
import WorkflowPage from './pages/Workflow'
import StreamStudioPage from './pages/StreamStudio'
import StreamMonitorPage from './pages/StreamMonitor'
import StreamOverviewPage from './pages/StreamOverview'
import FlinkSessionProfilesPage from './pages/FlinkSessionProfiles'
import SystemRbacPage from './pages/SystemRbac'
import WorkspaceSettingsPage from './pages/WorkspaceSettings'
import ServiceOverviewPage from './pages/service/ServiceOverviewPage'
import ServiceApisPage from './pages/service/ServiceApisPage'
import ServiceAppsPage from './pages/service/ServiceAppsPage'
import ServiceMonitorPage from './pages/service/ServiceMonitorPage'
import ServiceGatewayPage from './pages/service/ServiceGatewayPage'
import { R } from './routes'
import RequireGidoBatchRoute from './components/RequireGidoBatchRoute'
import RequireServiceRoute from './components/RequireServiceRoute'
import { useAppStore } from './store'
import { defaultBatchHome } from './workspaceMenuPolicy'
import { defaultServiceHome } from './serviceMenuPolicy'

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

export default function App() {
  return (
    <ShellThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path={R.login} element={<LoginPage />} />
          <Route path={R.about} element={<AboutPage />} />

          <Route path={R.batch.root} element={<RequireAuth><MainLayout /></RequireAuth>}>
            <Route index element={<BatchIndexRedirect />} />
            <Route path="studio" element={<RequireGidoBatchRoute><StudioPage /></RequireGidoBatchRoute>} />
            <Route path="workflow" element={<RequireGidoBatchRoute><WorkflowPage /></RequireGidoBatchRoute>} />
            <Route path="datamap" element={<RequireGidoBatchRoute><DataMapPage /></RequireGidoBatchRoute>} />
            <Route path="probe" element={<RequireGidoBatchRoute><ProbePage /></RequireGidoBatchRoute>} />
            <Route path="quality" element={<RequireGidoBatchRoute><QualityPage /></RequireGidoBatchRoute>} />
            <Route path="integration" element={<RequireGidoBatchRoute><IntegrationPage /></RequireGidoBatchRoute>} />
            <Route path="operation" element={<RequireGidoBatchRoute><OperationPage /></RequireGidoBatchRoute>} />
            <Route path="approval" element={<RequireGidoBatchRoute><ApprovalPage /></RequireGidoBatchRoute>} />
            <Route path="dataservice" element={<Navigate to={R.service.apis} replace />} />
            <Route path="datasource" element={<RequireGidoBatchRoute><DatasourcePage /></RequireGidoBatchRoute>} />
            <Route path="workspace-settings" element={<RequireGidoBatchRoute><WorkspaceSettingsPage /></RequireGidoBatchRoute>} />
            <Route path="admin" element={<RequireGidoBatchRoute><SystemRbacPage /></RequireGidoBatchRoute>} />
            <Route path="system/integration" element={<RequireGidoBatchRoute><SystemRbacPage view="integration" /></RequireGidoBatchRoute>} />
          </Route>

          <Route path={R.stream.root} element={<RequireAuth><StreamLayout /></RequireAuth>}>
            <Route index element={<Navigate to={R.stream.studio} replace />} />
            <Route path="studio" element={<StreamStudioPage />} />
            <Route path="monitor" element={<StreamMonitorPage />} />
            <Route path="overview" element={<StreamOverviewPage />} />
            <Route path="flink-sessions" element={<FlinkSessionProfilesPage />} />
            <Route path="approval" element={<ApprovalPage />} />
          </Route>

          <Route path={R.service.root} element={<RequireAuth><ServiceLayout /></RequireAuth>}>
            <Route index element={<ServiceIndexRedirect />} />
            <Route path="overview" element={<RequireServiceRoute><ServiceOverviewPage /></RequireServiceRoute>} />
            <Route path="apis" element={<RequireServiceRoute><ServiceApisPage /></RequireServiceRoute>} />
            <Route path="apps" element={<RequireServiceRoute><ServiceAppsPage /></RequireServiceRoute>} />
            <Route path="monitor" element={<RequireServiceRoute><ServiceMonitorPage /></RequireServiceRoute>} />
            <Route path="gateway" element={<RequireServiceRoute><ServiceGatewayPage /></RequireServiceRoute>} />
            <Route path="datasource" element={<RequireServiceRoute><DatasourcePage /></RequireServiceRoute>} />
            <Route path="approval" element={<RequireServiceRoute><ApprovalPage /></RequireServiceRoute>} />
          </Route>

          <Route path="/" element={<RootRedirect />} />
          <Route path="*" element={<RootRedirect />} />
        </Routes>
      </BrowserRouter>
    </ShellThemeProvider>
  )
}
