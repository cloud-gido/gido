/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import request from './request'

// 认证
export const authApi = {
  login: (username: string, password: string) =>
    request.post(
      '/auth/login',
      { username: username.trim(), password },
      { headers: { 'Content-Type': 'application/json' } },
    ),
  me: () => request.get('/auth/me'),
  register: (data: any) => request.post('/auth/register', data),
  changePassword: (current_password: string, new_password: string) =>
    request.post('/auth/change-password', { current_password, new_password }),
}

// 工作空间
export const workspaceApi = {
  list: () => request.get('/workspaces'),
  create: (data: any) => request.post('/workspaces', data),
  get: (id: number) => request.get(`/workspaces/${id}`),
  update: (id: number, data: any) => request.put(`/workspaces/${id}`, data),
  members: (id: number) => request.get(`/workspaces/${id}/members`),
  /** 空间管理员添加成员下拉：无需 platform system:user:read */
  inviteUserCandidates: (workspaceId: number) =>
    request.get(`/workspaces/${workspaceId}/invite-user-candidates`),
  addMember: (id: number, data: any) => request.post(`/workspaces/${id}/members`, data),
  removeMember: (workspaceId: number, memberUserId: number) =>
    request.delete(`/workspaces/${workspaceId}/members/${memberUserId}`),
  getDefaults: (workspaceId: number) => request.get(`/workspaces/${workspaceId}/settings/defaults`),
  putDefaults: (workspaceId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/settings/defaults`, data),
  getDolphin: (workspaceId: number) => request.get(`/workspaces/${workspaceId}/settings/dolphin`),
  putDolphin: (workspaceId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/settings/dolphin`, data),
  testDolphin: (workspaceId: number) => request.post(`/workspaces/${workspaceId}/settings/dolphin/test`),
  getFlink: (workspaceId: number) => request.get(`/workspaces/${workspaceId}/settings/flink`),
  putFlink: (workspaceId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/settings/flink`, data),
}

// 数据源
export const datasourceApi = {
  list: (workspaceId: number) => request.get('/datasources', { params: { workspace_id: workspaceId } }),
  create: (data: any) => request.post('/datasources', data),
  get: (id: number) => request.get(`/datasources/${id}`),
  update: (id: number, data: any) => request.put(`/datasources/${id}`, data),
  delete: (id: number) => request.delete(`/datasources/${id}`),
  test: (id: number) => request.post(`/datasources/${id}/test`),
}

// 数据开发
export const studioApi = {
  listNodes: (workspaceId: number, folderId?: number) => request.get('/studio/nodes', { params: { workspace_id: workspaceId, folder_id: folderId } }),
  createNode: (data: any) => request.post('/studio/nodes', data),
  getNode: (id: number) => request.get(`/studio/nodes/${id}`),
  updateNode: (id: number, data: any) => request.put(`/studio/nodes/${id}`, data),
  deleteNode: (id: number) => request.delete(`/studio/nodes/${id}`),
  publishNode: (id: number) => request.post(`/studio/nodes/${id}/publish`),
  unlockNode: (id: number) => request.post(`/studio/nodes/${id}/unlock`),
  acquireEditLock: (id: number, force?: boolean) =>
    request.post(`/studio/nodes/${id}/acquire-edit-lock`, null, { params: { force: force ? true : undefined } }),
  releaseEditLock: (id: number) => request.post(`/studio/nodes/${id}/release-edit-lock`),
  moveNodeFolder: (id: number, folder_id: number | null) =>
    request.patch(`/studio/nodes/${id}/folder`, { folder_id }),
  reorderNodes: (workspace_id: number, folder_id: number | null, node_ids: number[]) =>
    request.put('/studio/nodes/reorder', { workspace_id, folder_id, node_ids }),
  runNode: (id: number, scriptContent?: string) => request.post(`/studio/nodes/${id}/run`, null, { params: scriptContent !== undefined ? { script_content: scriptContent } : {} }),
  getInstances: (id: number) => request.get(`/studio/nodes/${id}/instances`),
  getHistory: (id: number) => request.get(`/studio/nodes/${id}/history`),
  rollback: (id: number, historyId: number) => request.post(`/studio/nodes/${id}/history/${historyId}/rollback`),
  getDependencies: (id: number) => request.get(`/studio/nodes/${id}/dependencies`),
  addDependency: (id: number, dependsOnId: number) => request.post(`/studio/nodes/${id}/dependencies`, null, { params: { depends_on_id: dependsOnId } }),
  listFolders: (workspaceId: number) => request.get('/studio/folders', { params: { workspace_id: workspaceId } }),
  createFolder: (data: any) => request.post('/studio/folders', data),
  renameFolder: (id: number, name: string) => request.put(`/studio/folders/${id}`, null, { params: { name } }),
  deleteFolder: (id: number) => request.delete(`/studio/folders/${id}`),
}

// 工作流
export const workflowApi = {
  list: (workspaceId: number) => request.get('/workflows', { params: { workspace_id: workspaceId } }),
  create: (data: any) => request.post('/workflows', data),
  get: (id: number) => request.get(`/workflows/${id}`),
  update: (id: number, data: any) => request.put(`/workflows/${id}`, data),
  delete: (id: number) => request.delete(`/workflows/${id}`),
  run: (id: number, businessDate?: string) => request.post(`/workflows/${id}/run`, null, { params: { business_date: businessDate } }),
  publishToDS: (id: number) => request.post(`/workflows/${id}/publish-to-ds`),
  bulkPublishToDS: (workspaceId: number) =>
    request.post('/workflows/bulk-publish-to-ds', { workspace_id: workspaceId }),
  instances: (id: number) => request.get(`/workflows/${id}/instances`),
  rerun: (wfId: number, instId: number) => request.post(`/workflows/${wfId}/instances/${instId}/rerun`),
  batchRun: (id: number, startDate: string, endDate: string) =>
    request.post(`/workflows/${id}/batch-run`, null, { params: { start_date: startDate, end_date: endDate } }),
}

// 数据探查（只读 SQL）
export const probeApi = {
  query: (data: { workspace_id: number; datasource_id: number; sql: string; limit?: number }) =>
    request.post('/probe/query', data),
}

// 数据集成
export const integrationApi = {
  supportedTypes: () => request.get('/integration/meta/supported-types'),
  listTasks: (workspaceId: number) => request.get('/integration/tasks', { params: { workspace_id: workspaceId } }),
  createTask: (data: any) => request.post('/integration/tasks', data),
  getTask: (id: number) => request.get(`/integration/tasks/${id}`),
  updateTask: (id: number, data: any) => request.put(`/integration/tasks/${id}`, data),
  deleteTask: (id: number) => request.delete(`/integration/tasks/${id}`),
  toggleActive: (id: number) => request.post(`/integration/tasks/${id}/toggle-active`),
  validateTask: (id: number) => request.post(`/integration/tasks/${id}/validate`),
  runTask: (id: number) => request.post(`/integration/tasks/${id}/run`),
  records: (id: number, limit?: number) =>
    request.get(`/integration/tasks/${id}/records`, { params: limit ? { limit } : {} }),
  getRecord: (taskId: number, recordId: number) =>
    request.get(`/integration/tasks/${taskId}/records/${recordId}`),
  listTables: (datasourceId: number, keyword?: string) =>
    request.get(`/integration/datasources/${datasourceId}/tables`, { params: { keyword: keyword || '' } }),
  getColumns: (datasourceId: number, tableName: string) =>
    request.get('/integration/datasource-columns', { params: { datasource_id: datasourceId, table_name: tableName } }),
  testDatasource: (datasourceId: number) => request.post(`/integration/datasources/${datasourceId}/test`),
  cdcStart: (id: number) => request.post(`/integration/tasks/${id}/cdc/start`),
  cdcStop: (id: number) => request.post(`/integration/tasks/${id}/cdc/stop`),
  cdcStatus: (id: number) => request.get(`/integration/tasks/${id}/cdc/status`),
}

// 数据地图
export const datamapApi = {
  searchTables: (workspaceId: number, keyword?: string) => request.get('/datamap/tables', { params: { workspace_id: workspaceId, keyword } }),
  /** 数据源物理表 + 已注册元数据合并（MySQL） */
  catalog: (workspaceId: number, params?: { datasource_id?: number; keyword?: string }) =>
    request.get('/datamap/catalog', { params: { workspace_id: workspaceId, ...params } }),
  registerTable: (data: any) => request.post('/datamap/tables', data),
  getTable: (id: number) => request.get(`/datamap/tables/${id}`),
  syncSchema: (id: number) => request.post(`/datamap/tables/${id}/sync-schema`),
  addColumn: (tableId: number, data: any) => request.post(`/datamap/tables/${tableId}/columns`, data),
  addLineage: (data: any) => request.post('/datamap/lineage', data),
  getLineage: (tableId: number, depth?: number) => request.get(`/datamap/lineage/${tableId}`, { params: { depth } }),
  getImpact: (tableId: number) => request.get(`/datamap/lineage/${tableId}/impact`),
  previewData: (tableId: number, limit?: number) => request.get(`/datamap/tables/${tableId}/preview`, { params: { limit } }),
}

// 数据质量
export const qualityApi = {
  listRules: (workspaceId: number) => request.get('/quality/rules', { params: { workspace_id: workspaceId } }),
  createRule: (data: any) => request.post('/quality/rules', data),
  deleteRule: (id: number) => request.delete(`/quality/rules/${id}`),
  runCheck: (id: number) => request.post(`/quality/rules/${id}/check`),
  records: (id: number) => request.get(`/quality/rules/${id}/records`),
  dashboard: (workspaceId: number) => request.get('/quality/dashboard', { params: { workspace_id: workspaceId } }),
  trend: (ruleId: number, days?: number) => request.get(`/quality/rules/${ruleId}/trend`, { params: { days } }),
  workspaceTrend: (workspaceId: number, days?: number) => request.get('/quality/workspace-trend', { params: { workspace_id: workspaceId, days } }),
}

// 调度器（Dolphin 元数据同步等）
export const schedulerApi = {
  syncDolphinInstances: () => request.post('/scheduler/ds/sync-instances'),
}

// 运维中心
export const operationApi = {
  overview: (workspaceId: number, params?: Record<string, unknown>) =>
    request.get('/operation/overview', { params: { workspace_id: workspaceId, ...params } }),
  instances: (workspaceId: number, params?: any) => request.get('/operation/instances', { params: { workspace_id: workspaceId, ...params } }),
  nodeInstances: (workspaceId: number, params?: any) => request.get('/operation/node-instances', { params: { workspace_id: workspaceId, ...params } }),
  getLog: (niId: number) => request.get(`/operation/node-instances/${niId}/log`),
  kill: (niId: number) => request.post(`/operation/node-instances/${niId}/kill`),
  retry: (niId: number) => request.post(`/operation/node-instances/${niId}/retry`),
  alerts: (workspaceId: number) => request.get('/operation/alerts', { params: { workspace_id: workspaceId } }),
}

// 发布审批
export const approvalApi = {
  list: (workspaceId: number, params?: Record<string, unknown>) =>
    request.get('/approvals', { params: { workspace_id: workspaceId, ...params } }),
  submit: (data: {
    workspace_id: number
    resource_type: string
    resource_id: number
    action: string
    submit_note?: string
  }) => request.post('/approvals/submit', data),
  pendingCount: (workspaceId: number) =>
    request.get('/approvals/pending-count', { params: { workspace_id: workspaceId } }),
  approve: (id: number, review_note?: string) =>
    request.post(`/approvals/${id}/approve`, { review_note }),
  reject: (id: number, review_note?: string) =>
    request.post(`/approvals/${id}/reject`, { review_note }),
  cancel: (id: number) => request.post(`/approvals/${id}/cancel`),
}

// 审计日志
export const auditApi = {
  list: (params?: any) => request.get('/audit/logs', { params }),
}

// 系统管理（RBAC）
export const adminApi = {
  listPermissions: () => request.get('/admin/permissions'),
  listRoles: () => request.get('/admin/roles'),
  createRole: (data: { code: string; name: string; description?: string; permission_codes: string[] }) =>
    request.post('/admin/roles', data),
  updateRole: (id: number, data: { name?: string; description?: string; permission_codes?: string[] }) =>
    request.put(`/admin/roles/${id}`, data),
  deleteRole: (id: number) => request.delete(`/admin/roles/${id}`),
  listUsers: () => request.get('/admin/users'),
  createUser: (data: { username: string; email: string; password: string; full_name?: string; role_id?: number }) =>
    request.post('/admin/users', data),
  setUserRole: (userId: number, role_id: number) => request.put(`/admin/users/${userId}/role`, { role_id }),
  setUserFlags: (userId: number, body: { is_admin?: boolean; is_active?: boolean }) =>
    request.put(`/admin/users/${userId}/flags`, body),
  deleteUser: (userId: number) => request.delete(`/admin/users/${userId}`),
  getDolphinIntegration: () => request.get('/admin/integration/dolphin'),
  putDolphinIntegration: (data: Record<string, unknown>) => request.put('/admin/integration/dolphin', data),
  testDolphinIntegration: () => request.post('/admin/integration/dolphin/test'),
  resetDolphinIntegration: () => request.post('/admin/integration/dolphin/reset-overrides'),
  getFlinkIntegration: () => request.get('/admin/integration/flink'),
  putFlinkIntegration: (data: Record<string, unknown>) => request.put('/admin/integration/flink', data),
  testFlinkIntegration: () => request.post('/admin/integration/flink/test'),
  flinkDeployHint: () => request.post('/admin/integration/flink/deploy-hint'),
  resetFlinkIntegration: () => request.post('/admin/integration/flink/reset-overrides'),
  /** 拦截器返回 res.data；responseType text 时实为 string，此处断言供 tsc 通过 */
  flinkSqlGatewayK8sYml: (): Promise<string> =>
    request.get('/admin/integration/flink/sql-gateway-k8s-yml', { responseType: 'text' as const }) as Promise<string>,
}

// 实时开发
export const streamingApi = {
  overview: (params?: { workspace_id?: number; flink_session_profile_id?: number }) =>
    request.get('/streaming/overview', { params }),
  connectivity: (params?: { workspace_id?: number; flink_session_profile_id?: number }) =>
    request.get('/streaming/connectivity', { params }),
  listFlinkSessionProfiles: (workspaceId: number) =>
    request.get('/streaming/flink-session-profiles', { params: { workspace_id: workspaceId } }),
  flinkPlatformDefaults: (workspaceId: number) =>
    request.get('/streaming/flink-platform-defaults', { params: { workspace_id: workspaceId } }),
  flinkRuntime: () => request.get('/streaming/flink-runtime'),
  createFlinkSessionProfile: (data: any) => request.post('/streaming/flink-session-profiles', data),
  updateFlinkSessionProfile: (id: number, data: any) => request.put(`/streaming/flink-session-profiles/${id}`, data),
  deleteFlinkSessionProfile: (id: number) => request.delete(`/streaming/flink-session-profiles/${id}`),
  listJobs: (workspaceId: number) => request.get('/streaming/jobs', { params: { workspace_id: workspaceId } }),
  createJob: (data: any) => request.post('/streaming/jobs', data),
  updateJob: (id: number, data: any) => request.put(`/streaming/jobs/${id}`, data),
  unlockJob: (id: number) => request.post(`/streaming/jobs/${id}/unlock`),
  getJobHistory: (jobId: number) => request.get(`/streaming/jobs/${jobId}/history`),
  rollbackJobHistory: (jobId: number, historyId: number) =>
    request.post(`/streaming/jobs/${jobId}/history/${historyId}/rollback`),
  deleteJob: (id: number) => request.delete(`/streaming/jobs/${id}`),
  submitJob: (id: number, scriptContent?: string) =>
    request.post(
      `/streaming/jobs/${id}/submit`,
      scriptContent !== undefined ? { script_content: scriptContent } : {},
    ),
  cancelJob: (id: number) => request.post(`/streaming/jobs/${id}/cancel`),
  getStatus: (id: number) => request.get(`/streaming/jobs/${id}/status`),
  getExceptions: (id: number) => request.get(`/streaming/jobs/${id}/exceptions`),
  uploadJar: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/streaming/jobs/${id}/upload-jar`, form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}

// 数据服务
export const dataServiceApi = {
  listApis: (workspaceId: number, status?: string) =>
    request.get('/data-service/apis', { params: { workspace_id: workspaceId, status } }),
  createApi: (data: any) => request.post('/data-service/apis', data),
  getApi: (id: number) => request.get(`/data-service/apis/${id}`),
  updateApi: (id: number, data: any) => request.put(`/data-service/apis/${id}`, data),
  deleteApi: (id: number) => request.delete(`/data-service/apis/${id}`),
  publishApi: (id: number) => request.post(`/data-service/apis/${id}/publish`),
  offlineApi: (id: number) => request.post(`/data-service/apis/${id}/offline`),
  testApi: (id: number, data: any) => request.post(`/data-service/apis/${id}/test`, data),
  openapi: (id: number) => request.get(`/data-service/apis/${id}/openapi`),
  listApps: (workspaceId: number) => request.get('/data-service/apps', { params: { workspace_id: workspaceId } }),
  createApp: (data: any) => request.post('/data-service/apps', data),
  deleteApp: (id: number) => request.delete(`/data-service/apps/${id}`),
  grantApis: (appId: number, data: { api_ids: number[]; qps_limit?: number }) =>
    request.post(`/data-service/apps/${appId}/grants`, data),
  revokeGrant: (appId: number, apiId: number) => request.delete(`/data-service/apps/${appId}/grants/${apiId}`),
  stats: (workspaceId: number, days?: number) =>
    request.get('/data-service/stats', { params: { workspace_id: workspaceId, days } }),
  logs: (workspaceId: number, params?: { api_id?: number; limit?: number }) =>
    request.get('/data-service/logs', { params: { workspace_id: workspaceId, ...params } }),
  previewWizardSql: (data: any) => request.post('/data-service/wizard/preview-sql', data),
}
