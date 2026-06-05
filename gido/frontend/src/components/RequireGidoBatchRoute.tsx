/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Navigate, useLocation } from 'react-router-dom'
import { useAppStore } from '../store'
import { canAccessBatchPath, defaultBatchHome } from '../workspaceMenuPolicy'

/** 防止只读用户通过 URL 直接进入开发/工作流等页面 */
export default function RequireGidoBatchRoute({ children }: { children: JSX.Element }) {
  const { user, currentWorkspace } = useAppStore()
  const { pathname } = useLocation()
  if (canAccessBatchPath(user, currentWorkspace, pathname)) {
    return children
  }
  return <Navigate to={defaultBatchHome(user, currentWorkspace)} replace />
}
