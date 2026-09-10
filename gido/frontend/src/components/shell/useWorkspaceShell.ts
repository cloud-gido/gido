/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useRef, useState } from 'react'
import { Form, message } from 'antd'
import { workspaceApi, authApi } from '../../api'
import { useAppStore } from '../../store'
import { pickDefaultWorkspace } from '../../workspacePick'
import { workspaceSwitcherLabel } from '../../utils/roleLabels'

/** 模块级：同一 SPA 会话内空间列表 / me 只主动拉一次（切子产品不再重复） */
let workspacesFetchStarted = false
let meFetchStarted = false

export function __resetWorkspaceShellFetchGuardsForTests() {
  workspacesFetchStarted = false
  meFetchStarted = false
}

export function useWorkspaceShell() {
  const {
    user,
    currentWorkspace,
    setCurrentWorkspace,
    setUser,
    workspaces,
    setWorkspaces,
  } = useAppStore()
  const [tzModal, setTzModal] = useState(false)
  const [tzForm] = Form.useForm()
  const [createWsOpen, setCreateWsOpen] = useState(false)
  const [wsForm] = Form.useForm()
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const loadWorkspaces = async (force = false) => {
    if (!force && workspacesFetchStarted && workspaces.length > 0) return
    workspacesFetchStarted = true
    try {
      const res: any[] = await workspaceApi.list() as any
      if (mounted.current) setWorkspaces(res)
    } catch {
      workspacesFetchStarted = false
    }
  }

  useEffect(() => {
    void loadWorkspaces()
    // 仅挂载时拉列表；统一壳下切子产品不 remount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (meFetchStarted) return
    meFetchStarted = true
    authApi.me().then((u: any) => {
      if (mounted.current) setUser(u)
    }).catch(() => {
      meFetchStarted = false
    })
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
    setWorkspaces(workspaces.map(w => w.id === currentWorkspace!.id ? { ...w, timezone } : w))
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
    await loadWorkspaces(true)
    setCurrentWorkspace(created)
  }

  const wsLabel = (w: any) => workspaceSwitcherLabel(w)

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
