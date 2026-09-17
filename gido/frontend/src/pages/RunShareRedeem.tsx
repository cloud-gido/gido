/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Spin } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import { adhocRunsApi, workspaceApi } from '../api'
import { useAppStore } from '../store'
import { R } from '../routes'

export default function RunShareRedeemPage() {
  const { token } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const setCurrentWorkspace = useAppStore(state => state.setCurrentWorkspace)
  const started = useRef(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!token || started.current) return
    started.current = true
    adhocRunsApi.redeemShare(token)
      .then(async redeemed => {
        const workspaces = await workspaceApi.list() as unknown as any[]
        const workspace = workspaces.find(item => Number(item.id) === Number(redeemed.run.workspace_id))
        if (!workspace) throw new Error('分享所属空间当前不可用')
        setCurrentWorkspace(workspace)
        navigate(`${R.batch.runHistory}/${redeemed.run.id}`, { replace: true })
      })
      .catch((reason: any) => {
        setError(reason?.response?.data?.detail || reason?.message || '兑换分享链接失败')
      })
  }, [navigate, setCurrentWorkspace, token])

  return (
    <Card title="打开运行分享" style={{ maxWidth: 560, margin: '48px auto' }}>
      {error ? (
        <>
          <Alert type="error" showIcon message="无法打开分享链接" description={error} />
          <Button style={{ marginTop: 16 }} onClick={() => navigate(R.batch.runHistory)}>返回运行历史</Button>
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: 32 }}>
          <Spin />
          <div style={{ marginTop: 12 }}>正在验证空间权限并打开统一运行详情…</div>
        </div>
      )}
    </Card>
  )
}
