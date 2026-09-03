/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
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
  updateAvatar: (avatar: string | null) => request.patch('/auth/me/avatar', { avatar }),
  uploadAvatar: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request.post('/auth/me/avatar/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
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
  getCopilot: (workspaceId: number) => request.get(`/workspaces/${workspaceId}/settings/copilot`),
  putCopilot: (workspaceId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/settings/copilot`, data),
  testCopilot: (workspaceId: number) => request.post(`/workspaces/${workspaceId}/settings/copilot/test`),
  getFlink: (workspaceId: number) => request.get(`/workspaces/${workspaceId}/settings/flink`),
  putFlink: (workspaceId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/settings/flink`, data),
  listVariables: (workspaceId: number, scope?: string) =>
    request.get(`/workspaces/${workspaceId}/variables`, { params: scope ? { scope } : {} }),
  createVariable: (workspaceId: number, data: Record<string, unknown>) =>
    request.post(`/workspaces/${workspaceId}/variables`, data),
  updateVariable: (workspaceId: number, varId: number, data: Record<string, unknown>) =>
    request.put(`/workspaces/${workspaceId}/variables/${varId}`, data),
  deleteVariable: (workspaceId: number, varId: number) =>
    request.delete(`/workspaces/${workspaceId}/variables/${varId}`),
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
  /** 列表默认不含脚本正文（加速进入数据开发）；打开编辑用 getNode */
  listNodes: (workspaceId: number, folderId?: number) =>
    request.get('/studio/nodes', {
      params: { workspace_id: workspaceId, folder_id: folderId },
    }),
  createNode: (data: any) => request.post('/studio/nodes', data),
  getNode: (id: number) => request.get(`/studio/nodes/${id}`),
  /** createHistory=false：静默草稿保存，不写版本历史（编辑器自动保存） */
  updateNode: (id: number, data: any, opts?: { createHistory?: boolean }) =>
    request.put(`/studio/nodes/${id}`, data, {
      params: { create_history: opts?.createHistory !== false },
    }),
  saveDraft: (
    id: number,
    data: { workspace_id: number; name: string; node_type: string; script_content: string },
  ) => request.put(`/studio/nodes/${id}`, data, { params: { create_history: false } }),
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
  runNode: (id: number, scriptContent?: string) =>
    request.post(
      `/studio/nodes/${id}/run`,
      scriptContent !== undefined ? { script_content: scriptContent } : {},
    ),
  getInstances: (id: number) => request.get(`/studio/nodes/${id}/instances`),
  getHistory: (id: number) => request.get(`/studio/nodes/${id}/history`),
  rollback: (id: number, historyId: number) => request.post(`/studio/nodes/${id}/history/${historyId}/rollback`),
  getDependencies: (id: number) => request.get(`/studio/nodes/${id}/dependencies`),
  addDependency: (id: number, dependsOnId: number) => request.post(`/studio/nodes/${id}/dependencies`, null, { params: { depends_on_id: dependsOnId } }),
  listFolders: (workspaceId: number) => request.get('/studio/folders', { params: { workspace_id: workspaceId } }),
  createFolder: (data: any) => request.post('/studio/folders', data),
  renameFolder: (id: number, name: string) => request.put(`/studio/folders/${id}`, null, { params: { name } }),
  moveFolderParent: (id: number, parent_id: number | null) =>
    request.patch(`/studio/folders/${id}/parent`, { parent_id }),
  reorderFolders: (workspace_id: number, parent_id: number | null, folder_ids: number[]) =>
    request.put('/studio/folders/reorder', { workspace_id, parent_id, folder_ids }),
  deleteFolder: (id: number) => request.delete(`/studio/folders/${id}`),
}

// 工作流
export const workflowApi = {
  list: (
    workspaceId: number,
    params?: {
      page?: number
      page_size?: number
      keyword?: string
      created_by?: number
      created_by_username?: string
      /** draft|published|paused|offline|all；默认后端 published */
      status?: string
    },
  ) =>
    request.get('/workflows', {
      params: {
        workspace_id: workspaceId,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
        keyword: params?.keyword || undefined,
        created_by: params?.created_by,
        created_by_username: params?.created_by_username || undefined,
        status: params?.status ?? 'published',
      },
    }),
  /** 依赖选择等场景：拉全量摘要（无 DAG） */
  listAll: (workspaceId: number, pageSize = 500) =>
    request.get('/workflows', {
      params: { workspace_id: workspaceId, page: 1, page_size: pageSize, status: 'all' },
    }),
  create: (data: any) => request.post('/workflows', data),
  get: (id: number) => request.get(`/workflows/${id}`),
  update: (id: number, data: any) => request.put(`/workflows/${id}`, data),
  delete: (id: number) => request.delete(`/workflows/${id}`),
  run: (id: number, businessDate?: string) => request.post(`/workflows/${id}/run`, null, { params: { business_date: businessDate } }),
  publishToDS: (id: number) => request.post(`/workflows/${id}/publish-to-ds`),
  pause: (id: number) => request.post(`/workflows/${id}/pause`),
  resume: (id: number) => request.post(`/workflows/${id}/resume`),
  offline: (id: number) => request.post(`/workflows/${id}/offline`),
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
  getTree: (workspaceId: number) =>
    request.get('/probe/tree', { params: { workspace_id: workspaceId } }),
  saveTree: (data: {
    workspace_id: number
    folders: unknown[]
    scripts: unknown[]
    activeScriptId: string | null
  }) => request.put('/probe/tree', data),
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
  uploadFileImport: (
    workspaceId: number,
    file: File,
    opts?: {
      encoding?: string
      delimiter?: string
      has_header?: boolean
      sheet_name?: string
      onProgress?: (percent: number, meta?: {
        loaded: number
        total: number
        speedBps: number
        etaSeconds: number | null
      }) => void
      onPhase?: (phase: 'uploading' | 'parsing') => void
      onStatus?: (info: { received: number; total: number; resumed: boolean; fileId: string }) => void
      signal?: AbortSignal
    },
  ) => {
    const CHUNK = 16 * 1024 * 1024
    // 大文件走可断点续传分片，规避 HTTP/2 长上传失败并支持网络中断恢复
    if (file.size > CHUNK) {
      return import('../utils/fileImportUpload').then(({ uploadFileImportResumable }) =>
        uploadFileImportResumable(workspaceId, file, opts),
      )
    }
    const fd = new FormData()
    fd.append('workspace_id', String(workspaceId))
    fd.append('file', file)
    if (opts?.encoding) fd.append('encoding', opts.encoding)
    if (opts?.delimiter != null) fd.append('delimiter', opts.delimiter)
    if (opts?.has_header != null) fd.append('has_header', String(opts.has_header))
    if (opts?.sheet_name) fd.append('sheet_name', opts.sheet_name)
    let speedEwma = 0
    let lastSampleAt = Date.now()
    let lastSampleLoaded = 0
    return request.post('/integration/file-import/upload', fd, {
      timeout: 0,
      signal: opts?.signal,
      onUploadProgress: (evt) => {
        if (!opts?.onProgress) return
        const total = evt.total || file.size || 0
        const loaded = evt.loaded || 0
        if (!total) {
          opts.onProgress(0)
          return
        }
        const now = Date.now()
        const dt = (now - lastSampleAt) / 1000
        if (dt >= 0.4) {
          const instant = Math.max(0, loaded - lastSampleLoaded) / dt
          speedEwma = speedEwma > 0 ? speedEwma * 0.72 + instant * 0.28 : instant
          lastSampleAt = now
          lastSampleLoaded = loaded
        }
        const remain = Math.max(0, total - loaded)
        const etaSeconds = speedEwma >= 8 * 1024 ? Math.ceil(remain / speedEwma) : null
        const pct = Math.min(99, Math.round((loaded / total) * 100))
        opts.onProgress(pct, { loaded, total, speedBps: speedEwma, etaSeconds })
      },
    })
  },
  fileImportUploadStatus: (workspaceId: number, fileId: string) =>
    request.get('/integration/file-import/upload-status', {
      params: { workspace_id: workspaceId, file_id: fileId },
    }),
  abortFileImportUpload: (workspaceId: number, fileId: string) => {
    const fd = new FormData()
    fd.append('workspace_id', String(workspaceId))
    fd.append('file_id', fileId)
    return request.post('/integration/file-import/upload-abort', fd, {
      timeout: 60000,
    })
  },
  previewFileImport: (data: any) => request.post('/integration/file-import/preview', data),
  previewFileImportDdl: (data: any) => request.post('/integration/file-import/preview-ddl', data),
  createFileImportTask: (data: any) => request.post('/integration/file-import/tasks', data),
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
  previewCron: (cron: string, count = 5) =>
    request.get('/scheduler/cron/preview', { params: { cron, count } }),
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
  stopWorkflowInstance: (workspaceId: number, wfId: number, instId: number) =>
    request.post(`/operation/workflows/${wfId}/instances/${instId}/stop`, null, { params: { workspace_id: workspaceId } }),
  refreshWorkflowInstance: (workspaceId: number, wfId: number, instId: number) =>
    request.post(`/operation/workflows/${wfId}/instances/${instId}/refresh`, null, { params: { workspace_id: workspaceId } }),
  rerunWorkflowInstance: (workspaceId: number, wfId: number, instId: number) =>
    request.post(`/operation/workflows/${wfId}/instances/${instId}/rerun`, null, { params: { workspace_id: workspaceId } }),
  retryFailedNodes: (workspaceId: number, wfId: number, instId: number) =>
    request.post(`/operation/workflows/${wfId}/instances/${instId}/retry-failed-nodes`, null, { params: { workspace_id: workspaceId } }),
  alerts: (workspaceId: number) => request.get('/operation/alerts', { params: { workspace_id: workspaceId } }),
}

// 运行历史（数据开发试跑 / 数据探查）
export const adhocRunsApi = {
  list: (workspaceId: number, params?: Record<string, unknown>) =>
    request.get('/adhoc-runs', { params: { workspace_id: workspaceId, ...params } }),
  get: (id: number) => request.get(`/adhoc-runs/${id}`),
}

// 告警中心
export const alertApi = {
  list: (workspaceId: number, params?: Record<string, unknown>) =>
    request.get('/alerts', { params: { workspace_id: workspaceId, ...params } }),
  ack: (id: number) => request.post(`/alerts/${id}/ack`),
  resolve: (id: number) => request.post(`/alerts/${id}/resolve`),
  notify: (id: number) => request.post(`/alerts/${id}/notify`, { force: true }),
  getNotificationConfig: (workspaceId: number) =>
    request.get('/alerts/notification/config', { params: { workspace_id: workspaceId } }),
  putNotificationConfig: (workspaceId: number, data: Record<string, unknown>) =>
    request.put('/alerts/notification/config', data, { params: { workspace_id: workspaceId } }),
  testNotificationConfig: (workspaceId: number, data: Record<string, unknown>) =>
    request.post('/alerts/notification/test', data, { params: { workspace_id: workspaceId } }),
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
    release_id?: number
  }) => request.post('/approvals/submit', data),
  pendingCount: (workspaceId: number) =>
    request.get('/approvals/pending-count', { params: { workspace_id: workspaceId } }),
  approve: (id: number, review_note?: string) =>
    request.post(`/approvals/${id}/approve`, { review_note }),
  reject: (id: number, review_note?: string) =>
    request.post(`/approvals/${id}/reject`, { review_note }),
  cancel: (id: number) => request.post(`/approvals/${id}/cancel`),
  preview: (id: number) => request.get(`/approvals/${id}/preview`),
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
  getApsWorkflowSchedule: () => request.get('/admin/integration/aps-workflow-schedule'),
  putApsWorkflowSchedule: (data: { enabled: boolean | null }) =>
    request.put('/admin/integration/aps-workflow-schedule', data),
  disableApsWorkflowSchedule: () => request.post('/admin/integration/aps-workflow-schedule/disable'),
  getFlinkIntegration: () => request.get('/admin/integration/flink'),
  putFlinkIntegration: (data: Record<string, unknown>) => request.put('/admin/integration/flink', data),
  testFlinkIntegration: () => request.post('/admin/integration/flink/test'),
  flinkDeployHint: () => request.post('/admin/integration/flink/deploy-hint'),
  resetFlinkIntegration: () => request.post('/admin/integration/flink/reset-overrides'),
  getCopilotIntegration: () => request.get('/admin/integration/copilot'),
  putCopilotIntegration: (data: Record<string, unknown>) => request.put('/admin/integration/copilot', data),
  testCopilotIntegration: () => request.post('/admin/integration/copilot/test'),
  resetCopilotIntegration: () => request.post('/admin/integration/copilot/reset-overrides'),
  /** 拦截器返回 res.data；responseType text 时实为 string，此处断言供 tsc 通过 */
  flinkSqlGatewayK8sYml: (): Promise<string> =>
    request.get('/admin/integration/flink/sql-gateway-k8s-yml', { responseType: 'text' as const }) as Promise<string>,
}

export type StreamPipelineMode = 'append' | 'upsert' | 'cdc'

export type StreamPipelineDefinition = {
  id?: number
  workspace_id: number
  name: string
  description?: string
  status?: 'draft' | 'validated' | 'pending_approval' | 'approved' | 'running' | 'failed'
  mode: StreamPipelineMode
  source: {
    datasource_id?: number
    connection_profile_id?: number
    connector: string
    table: string
    credential_ref?: string
    startup_mode?: string
    consumer_group?: string
    format?: string
  }
  schema: {
    evolution: 'strict' | 'additive'
    contract_id?: number
    version?: number
    source_columns?: Array<{ name: string; type: string; nullable?: boolean }>
    columns: Array<{ name: string; type: string; nullable?: boolean; primary_key?: boolean }>
  }
  mapping: {
    fields: Array<{ source: string; target: string; expression?: string }>
    filter?: string
  }
  sink: {
    catalog: string
    database: string
    table: string
    connection_profile_id?: number
    warehouse_ref?: string
    primary_keys: string[]
    partitions: string[]
    bucket?: number
  }
  runtime: {
    parallelism: number
    resource_tier?: string
    streaming_properties?: string
  }
  placement: {
    pool?: string
    namespace?: string
    node_selector?: Record<string, string>
    requested_mode?: 'dedicated' | 'grouped' | 'recommend-only'
    sla_tier?: 'best-effort' | 'standard' | 'high' | 'critical'
    expected_records_per_second?: number
    state_size_gb?: number
    security_domain?: string
  }
  created_at?: string
  updated_at?: string
}

export type StreamPipelineExplain = {
  generated_artifact: {
    kind: 'flink_sql' | 'runner_spec'
    content: string
    runner?: Record<string, unknown>
    redacted: true
  }
  schema_diff: Array<{ column: string; change: string; type?: string; detail?: string }>
  placement: { decision: string; resource_tier?: string; parallelism?: number; capacity?: string }
  risks: Array<{ code: string; level: 'low' | 'medium' | 'high' | 'blocker'; message: string; requires_confirmation?: boolean }>
  valid: boolean
  local_fallback?: boolean
}

export type StreamPipelineSpec = {
  spec_version: '1.0'
  kind: 'kafka_to_paimon'
  mode: StreamPipelineMode
  schema_evolution: 'strict' | 'additive'
  error_policy: 'fail-fast'
  schema_contract_id?: number
  schema_version?: number
  source: {
    connector: 'kafka'
    connection_profile_id: number
    topic: string
    consumer_group: string
    format: 'json' | 'debezium-json' | 'canal-json' | 'maxwell-json'
    startup_mode: 'earliest-offset' | 'latest-offset' | 'group-offsets'
    options: Record<string, string>
  }
  sink: {
    connector: 'paimon'
    connection_profile_id: number
    database: string
    table: string
    primary_keys: string[]
    partition_keys: string[]
    options: Record<string, string>
  }
  source_schema?: Array<{ name: string; data_type: string; nullable: boolean }>
  schema: Array<{ name: string; data_type: string; nullable: boolean }>
  transform?: { projections: Record<string, string>; filter?: string }
  description?: string
}

// 实时开发
export const streamingApi = {
  operatorOverview: (workspaceId: number) =>
    request.get('/streaming/operator-overview', { params: { workspace_id: workspaceId } }),
  flinkRuntime: () => request.get('/streaming/flink-runtime'),
  /** 列表默认不含 script_content / generated_artifact；打开编辑用 getJob */
  listJobs: (workspaceId: number) => request.get('/streaming/jobs', { params: { workspace_id: workspaceId } }),
  getJob: (id: number) => request.get(`/streaming/jobs/${id}`),
  createJob: (data: any) => request.post('/streaming/jobs', data),
  copyJob: (id: number, data?: { name?: string }) => request.post(`/streaming/jobs/${id}/copy`, data || {}),
  /** createHistory=false：静默草稿，不写 streaming job history */
  updateJob: (id: number, data: any, opts?: { createHistory?: boolean }) =>
    request.put(`/streaming/jobs/${id}`, data, {
      params: { create_history: opts?.createHistory !== false },
    }),
  saveDraft: (id: number, data: { script_content: string }) =>
    request.put(`/streaming/jobs/${id}`, data, { params: { create_history: false } }),
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
  listReleases: (id: number) => request.get(`/streaming/jobs/${id}/releases`),
  createRelease: (id: number, data?: Record<string, unknown>) =>
    request.post(`/streaming/jobs/${id}/releases`, data || {}),
  deployJob: (id: number, data: Record<string, unknown>) =>
    request.post(`/streaming/jobs/${id}/deploy`, data),
  stopJob: (id: number, data?: Record<string, unknown>) =>
    request.post(`/streaming/jobs/${id}/stop`, data || { mode: 'savepoint' }),
  restartJob: (id: number, data: Record<string, unknown>) =>
    request.post(`/streaming/jobs/${id}/restart`, data),
  getRestorePoints: (id: number) => request.get(`/streaming/jobs/${id}/restore-points`),
  getOperations: (id: number) => request.get(`/streaming/jobs/${id}/operations`),
  cancelJob: (id: number) => request.post(`/streaming/jobs/${id}/cancel`),
  getStatus: (id: number) => request.get(`/streaming/jobs/${id}/status`),
  /** 工作空间批量状态同步（替代 N 路 getStatus） */
  syncJobsStatus: (workspaceId: number) =>
    request.get('/streaming/jobs-status-sync', { params: { workspace_id: workspaceId } }),
  getExceptions: (id: number) => request.get(`/streaming/jobs/${id}/exceptions`),
  getCheckpoints: (id: number) => request.get(`/streaming/jobs/${id}/checkpoints`),
  uploadJar: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/streaming/jobs/${id}/upload-jar`, form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  previewSql: (data: { workspace_id: number; sql: string; limit?: number }) =>
    request.post('/streaming/preview-sql', data),
  listFolders: (workspaceId: number) =>
    request.get('/streaming/folders', { params: { workspace_id: workspaceId } }),
  createFolder: (data: { workspace_id: number; name: string; parent_id?: number | null }) =>
    request.post('/streaming/folders', data),
  renameFolder: (id: number, name: string) => request.put(`/streaming/folders/${id}`, { name }),
  moveFolderParent: (id: number, parent_id: number | null) =>
    request.patch(`/streaming/folders/${id}/parent`, { parent_id }),
  reorderFolders: (workspace_id: number, parent_id: number | null, folder_ids: number[]) =>
    request.put('/streaming/folders/reorder', { workspace_id, parent_id, folder_ids }),
  deleteFolder: (id: number) => request.delete(`/streaming/folders/${id}`),
  moveJobFolder: (id: number, folder_id: number | null) =>
    request.patch(`/streaming/jobs/${id}/folder`, { folder_id }),
  reorderJobs: (data: { workspace_id: number; folder_id: number | null; job_ids: number[] }) =>
    request.put('/streaming/jobs/reorder', data),
  listJarArtifacts: (workspaceId: number) =>
    request.get('/streaming/jar-artifacts', { params: { workspace_id: workspaceId } }),
  createJarArtifact: (data: { workspace_id: number; name: string; description?: string }) =>
    request.post('/streaming/jar-artifacts', data),
  getJarArtifact: (id: number) => request.get(`/streaming/jar-artifacts/${id}`),
  updateJarArtifact: (id: number, data: { name?: string; description?: string }) =>
    request.put(`/streaming/jar-artifacts/${id}`, data),
  deleteJarArtifact: (id: number) => request.delete(`/streaming/jar-artifacts/${id}`),
  uploadJarVersion: (artifactId: number, file: File, opts?: { change_note?: string; default_main_class?: string }) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/streaming/jar-artifacts/${artifactId}/versions`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: {
        change_note: opts?.change_note || undefined,
        default_main_class: opts?.default_main_class || undefined,
      },
    })
  },
  deprecateJarVersion: (versionId: number) => request.post(`/streaming/jar-versions/${versionId}/deprecate`),
  backfillJarArtifacts: (workspaceId: number) =>
    request.post('/streaming/jar-artifacts/backfill', null, { params: { workspace_id: workspaceId } }),

  listConnectorArtifacts: (workspaceId: number) =>
    request.get('/streaming/connector-artifacts', { params: { workspace_id: workspaceId } }),
  createConnectorArtifact: (data: { workspace_id: number; name: string; description?: string }) =>
    request.post('/streaming/connector-artifacts', data),
  getConnectorArtifact: (id: number) => request.get(`/streaming/connector-artifacts/${id}`),
  updateConnectorArtifact: (id: number, data: { name?: string; description?: string }) =>
    request.put(`/streaming/connector-artifacts/${id}`, data),
  deleteConnectorArtifact: (id: number) => request.delete(`/streaming/connector-artifacts/${id}`),
  uploadConnectorVersion: (artifactId: number, file: File, opts?: { change_note?: string }) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/streaming/connector-artifacts/${artifactId}/versions`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: { change_note: opts?.change_note || undefined },
    })
  },
  deprecateConnectorVersion: (versionId: number) =>
    request.post(`/streaming/connector-versions/${versionId}/deprecate`),

  listFileArtifacts: (workspaceId: number) =>
    request.get('/streaming/file-artifacts', { params: { workspace_id: workspaceId } }),
  createFileArtifact: (data: { workspace_id: number; name: string; description?: string }) =>
    request.post('/streaming/file-artifacts', data),
  getFileArtifact: (id: number) => request.get(`/streaming/file-artifacts/${id}`),
  updateFileArtifact: (id: number, data: { name?: string; description?: string }) =>
    request.put(`/streaming/file-artifacts/${id}`, data),
  deleteFileArtifact: (id: number) => request.delete(`/streaming/file-artifacts/${id}`),
  uploadFileVersion: (artifactId: number, file: File, opts?: { change_note?: string }) => {
    const form = new FormData()
    form.append('file', file)
    return request.post(`/streaming/file-artifacts/${artifactId}/versions`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: { change_note: opts?.change_note || undefined },
    })
  },
  deprecateFileVersion: (versionId: number) =>
    request.post(`/streaming/file-versions/${versionId}/deprecate`),

  /**
   * Typed Kafka → Paimon foundation contract. Definitions are regular streaming
   * jobs with definition_kind=pipeline; profile responses expose secret_ref_keys
   * only, and compile artifacts use runtime profile references.
   */
  listPipelineProfiles: (workspaceId: number) =>
    request.get('/streaming/pipeline/connection-profiles', { params: { workspace_id: workspaceId } }),
  createPipelineProfile: (data: Record<string, unknown>) =>
    request.post('/streaming/pipeline/connection-profiles', data),
  discoverPipelineProfile: (profileId: number) =>
    request.post(`/streaming/pipeline/connection-profiles/${profileId}/discover`),
  compilePipeline: (spec: StreamPipelineSpec) =>
    request.post('/streaming/pipeline/compile', spec),
  preflightPipeline: (spec: StreamPipelineSpec) =>
    request.post('/streaming/pipeline/preflight', spec),
  previewPipelinePlacement: (data: Record<string, unknown>) =>
    request.post('/streaming/pipeline/placement/preview', data),
  saveJobPipelineSpec: (jobId: number, spec: StreamPipelineSpec) =>
    request.put(`/streaming/pipeline/jobs/${jobId}/spec`, spec),
  getPipelineObservability: (jobId: number) =>
    request.get(`/streaming/pipeline/jobs/${jobId}/observability`),
  createPipelineSchemaContract: (data: Record<string, unknown>) =>
    request.post('/streaming/pipeline/schema-contracts', data),
  createPipelineSchemaVersion: (contractId: number, data: Record<string, unknown>) =>
    request.post(`/streaming/pipeline/schema-contracts/${contractId}/versions`, data),
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
  discardPendingApi: (id: number) => request.post(`/data-service/apis/${id}/discard-pending`),
  testApi: (id: number, data: any) => request.post(`/data-service/apis/${id}/test`, data),
  openapi: (id: number) => request.get(`/data-service/apis/${id}/openapi`),
  exportApiBundle: (id: number) => request.get(`/data-service/apis/${id}/bundle`),
  exportApisBundle: (data: { workspace_id: number; api_ids?: number[]; api_codes?: string[] }) =>
    request.post('/data-service/apis/export-bundle', data),
  importApisBundle: (data: {
    workspace_id: number
    bundle: any
    on_conflict?: 'skip' | 'overwrite' | 'fail'
    datasource_map?: Record<string, string>
  }) => request.post('/data-service/apis/import-bundle', data),
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
  previewWizardSql: (data: { wizard_config: any; params?: any[] }) =>
    request.post('/data-service/wizard/preview-sql', data),
  listTables: (datasourceId: number, keyword?: string) =>
    request.get(`/data-service/datasources/${datasourceId}/tables`, {
      params: { keyword: keyword || '' },
    }),
  listColumns: (datasourceId: number, tableName: string) =>
    request.get(`/data-service/datasources/${datasourceId}/columns`, {
      params: { table_name: tableName },
    }),
}

export { copilotApi, copilotChatStream } from './copilot'
export type { CopilotStatus, CopilotChatResponse, CopilotQueryResult } from './copilot'
