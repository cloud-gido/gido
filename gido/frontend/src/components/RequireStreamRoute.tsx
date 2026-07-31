/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Navigate } from 'react-router-dom'
import { can, P } from '../perm'
import { R } from '../routes'
import { useAppStore } from '../store'

export default function RequireStreamRoute({ children }: { children: JSX.Element }) {
  const { user, currentWorkspace } = useAppStore()
  if (can(user, P.GIDO_STREAM_READ, currentWorkspace)) return children
  return <Navigate to={R.batch.root} replace />
}
