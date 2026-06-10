/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useState } from 'react'
import { Form, message } from 'antd'
import { workspaceApi, authApi } from '../../api'
import { useAppStore } from '../../store'
import { pickDefaultWorkspace } from '../../workspacePick'

export function useWorkspaceShell() {
  const { user, currentWorkspace, setCurrentWorkspace, setUser } = useAppStore()
  const [workspaces, setWorkspaces] = useState<any[]>([])
  const [tzModal, setTzModal] = useState(false)
  const [tzForm] = Form.useForm()
  const [createWsOpen, setCreateWsOpen] = useState(false)
  const [wsForm] = Form.useForm()

  const loadWorkspaces = async () => {
    const res: any[] = await workspaceApi.list() as any
    setWorkspaces(res)
  }

  useEffect(() => {
    loadWorkspaces()
  }, [])

  useEffect(() => {
    authApi.me().then((u: any) => setUser(u)).catch(() => {})
  }, [setUser])

  useEffect(() => {
    if (!workspaces.length) return
    const pick = pickDefaultWorkspace(workspaces, user)
    if (!pick) return
    const invalid = currentWorkspace && !workspaces.some((w: any) => w.id === currentWorkspace.id)
    if (!currentWorkspace || invalid) {
      setCurrentWorkspace(pick)
    }
  }, [workspaces, user, currentWorkspace, setCurrentWorkspace])

  const openTzModal = () => {
    tzForm.setFieldsValue({ timezone: currentWorkspace?.timezone || 'Asia/Shanghai' })
    setTzModal(true)
  }

  const handleSaveTz = async () => {
    const { timezone } = await tzForm.validateFields()
    await workspaceApi.update(currentWorkspace!.id, { ...currentWorkspace, timezone })
    setCurrentWorkspace({ ...currentWorkspace, timezone })
    setWorkspaces(prev => prev.map(w => w.id === currentWorkspace!.id ? { ...w, timezone } : w))
    setTzModal(false)
    message.success(`时区已设置为 ${timezone}`)
  }

  const submitNewWorkspace = async () => {
    const v = await wsForm.validateFields()
    const created: any = await workspaceApi.create({
      name: v.name.trim(),
      description: v.description?.trim() || undefined,
      timezone: v.timezone || 'Asia/Shanghai',
    })
    message.success('工作空间已创建')
    setCreateWsOpen(false)
    wsForm.resetFields()
    await loadWorkspaces()
    setCurrentWorkspace(created)
  }

  const wsLabel = (w: any) => (w.my_role ? `${w.name} · ${w.my_role}` : w.name)

  return {
    user,
    currentWorkspace,
    setCurrentWorkspace,
    workspaces,
    wsLabel,
    tzModal,
    setTzModal,
    tzForm,
    openTzModal,
    handleSaveTz,
    createWsOpen,
    setCreateWsOpen,
    wsForm,
    submitNewWorkspace,
  }
}
