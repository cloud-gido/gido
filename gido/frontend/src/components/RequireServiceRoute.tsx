/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Navigate, useLocation } from 'react-router-dom'
import { useAppStore } from '../store'
import { canAccessServicePath, defaultServiceHome } from '../serviceMenuPolicy'

export default function RequireServiceRoute({ children }: { children: JSX.Element }) {
  const { user, currentWorkspace } = useAppStore()
  const { pathname } = useLocation()
  if (canAccessServicePath(user, currentWorkspace, pathname)) {
    return children
  }
  return <Navigate to={defaultServiceHome(user, currentWorkspace)} replace />
}
