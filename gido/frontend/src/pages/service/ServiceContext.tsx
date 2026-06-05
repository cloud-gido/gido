/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAppStore } from '../../store'
import { dataServiceApi, datasourceApi } from '../../api'

type ServiceData = {
  apis: any[]
  apps: any[]
  datasources: any[]
  stats: any
  logs: any[]
  loading: boolean
  reload: () => Promise<void>
}

const ServiceDataContext = createContext<ServiceData | null>(null)

export function ServiceDataProvider({ children }: { children: ReactNode }) {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const [apis, setApis] = useState<any[]>([])
  const [apps, setApps] = useState<any[]>([])
  const [datasources, setDatasources] = useState<any[]>([])
  const [stats, setStats] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  const reload = useCallback(async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const [a, ap, ds, st, lg] = await Promise.all([
        dataServiceApi.listApis(wsId),
        dataServiceApi.listApps(wsId),
        datasourceApi.list(wsId),
        dataServiceApi.stats(wsId),
        dataServiceApi.logs(wsId, { limit: 100 }),
      ])
      setApis(a as any)
      setApps(ap as any)
      setDatasources(ds as any)
      setStats(st as any)
      setLogs(lg as any)
    } finally {
      setLoading(false)
    }
  }, [wsId])

  useEffect(() => {
    reload()
  }, [reload])

  const value = useMemo(
    () => ({ apis, apps, datasources, stats, logs, loading, reload }),
    [apis, apps, datasources, stats, logs, loading, reload],
  )

  return <ServiceDataContext.Provider value={value}>{children}</ServiceDataContext.Provider>
}

export function useServiceData() {
  const ctx = useContext(ServiceDataContext)
  if (!ctx) throw new Error('useServiceData must be used within ServiceDataProvider')
  return ctx
}

export function useWorkspaceId() {
  const { currentWorkspace } = useAppStore()
  return currentWorkspace?.id
}
