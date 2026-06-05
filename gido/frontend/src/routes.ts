/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 路由：GIDO Batch /gido/batch*、GIDO Stream /gido/stream*、GIDO Serve /gido/service* */
export const R = {
  login: '/login',
  about: '/about',
  batch: {
    root: '/gido/batch',
    studio: '/gido/batch/studio',
    workflow: '/gido/batch/workflow',
    datamap: '/gido/batch/datamap',
    probe: '/gido/batch/probe',
    quality: '/gido/batch/quality',
    integration: '/gido/batch/integration',
    operation: '/gido/batch/operation',
    approval: '/gido/batch/approval',
    datasource: '/gido/batch/datasource',
    /** @deprecated 旧路径，仅用于菜单策略判断 */
    legacyService: '/gido/batch/dataservice',
    workspaceSettings: '/gido/batch/workspace-settings',
    admin: '/gido/batch/admin',
    systemIntegration: '/gido/batch/system/integration',
  },
  stream: {
    root: '/gido/stream',
    studio: '/gido/stream/studio',
    monitor: '/gido/stream/monitor',
    overview: '/gido/stream/overview',
    flinkSessions: '/gido/stream/flink-sessions',
    approval: '/gido/stream/approval',
  },
  service: {
    root: '/gido/service',
    overview: '/gido/service/overview',
    apis: '/gido/service/apis',
    apps: '/gido/service/apps',
    monitor: '/gido/service/monitor',
    gateway: '/gido/service/gateway',
    datasource: '/gido/service/datasource',
    approval: '/gido/service/approval',
  },
} as const

export type ProductId = 'batch' | 'stream' | 'service'
