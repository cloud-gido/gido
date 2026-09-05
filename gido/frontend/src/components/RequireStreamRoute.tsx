/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Navigate, useLocation } from 'react-router-dom'
import { R } from '../routes'
import { useAppStore } from '../store'
import { canAccessStreamPath, canEnterStreamProduct, defaultStreamHome } from '../streamMenuPolicy'

export default function RequireStreamRoute({ children }: { children: JSX.Element }) {
  const { user, currentWorkspace } = useAppStore()
  const location = useLocation()
  if (!canEnterStreamProduct(user, currentWorkspace)) {
    return <Navigate to={R.batch.root} replace />
  }
  if (!canAccessStreamPath(user, currentWorkspace, location.pathname)) {
    return <Navigate to={defaultStreamHome(user, currentWorkspace)} replace />
  }
  return children
}
