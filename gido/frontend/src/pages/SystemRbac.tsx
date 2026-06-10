/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useState, useMemo } from 'react'
import {
  Tabs, Table, Button, Space, Select, Switch, message, Modal, Form, Input, Checkbox, Tag, Popconfirm, Card, Descriptions, Alert, Row, Col, Typography,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined, UserAddOutlined, ApiOutlined, ExperimentOutlined,
  ThunderboltOutlined, FileTextOutlined, TeamOutlined, DownloadOutlined,
} from '@ant-design/icons'
import { adminApi, authApi, workspaceApi } from '../api'
import { useAppStore } from '../store'
import { can, isPlatformAdmin, P } from '../perm'
import { Link } from 'react-router-dom'
import { R } from '../routes'

type PermRow = { id: number; code: string; name: string; module: string }
type RoleRow = { id: number; code: string; name: string; description?: string; is_system: boolean; permission_codes: string[] }
type UserRow = { id: number; username: string; email: string; is_admin: boolean; is_active: boolean; role_id?: number; role_code?: string; role_name?: string }

export type SystemRbacPageProps = {
  /** integration：仅 Dolphin/Flink 集成（侧栏「平台集成」直达） */
  view?: 'full' | 'integration'
}

const { Title, Text } = Typography

export default function SystemRbacPage({ view = 'full' }: SystemRbacPageProps) {
  const { user: me, setUser, currentWorkspace } = useAppStore()
  const [perms, setPerms] = useState<PermRow[]>([])
  const [roles, setRoles] = useState<RoleRow[]>([])
  const [users, setUsers] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(false)
  const [roleModal, setRoleModal] = useState(false)
  const [userModal, setUserModal] = useState(false)
  const [editingRole, setEditingRole] = useState<RoleRow | null>(null)
  const [form] = Form.useForm()
  const [userForm] = Form.useForm()
  const [permPick, setPermPick] = useState<string[]>([])
  const [dolphinForm] = Form.useForm()
  const [dolphinLoading, setDolphinLoading] = useState(false)
  const [dolphinMeta, setDolphinMeta] = useState<any>(null)
  const [flinkForm] = Form.useForm()
  const [flinkLoading, setFlinkLoading] = useState(false)
  const [flinkMeta, setFlinkMeta] = useState<any>(null)
  const [deployModal, setDeployModal] = useState(false)
  const [deployHint, setDeployHint] = useState<any>(null)

  const canManageUsers = can(me, P.SYSTEM_USER_READ)
  const canWriteRole = can(me, P.SYSTEM_ROLE_WRITE)
  const canDeleteRole = can(me, P.SYSTEM_ROLE_DELETE)
  const canDeleteUser = can(me, P.SYSTEM_USER_DELETE)
  const canCreateUser = can(me, P.SYSTEM_USER_WRITE)
  const isSuper = isPlatformAdmin(me)
  /** 与工作空间 PUT owner、integration、创建空间等平台级能力对齐（≠ 单个空间的空间管理员）。 */
  const canWorkspacePlatformOps = isSuper
  /** 本空间管理员可管理成员；超级管理员同时具备。 */
  const canManageWorkspaceMembersUi = Boolean(
    canWorkspacePlatformOps || currentWorkspace?.my_role === 'admin',
  )
  // 与后端 admin/integration 上 require_platform_manager 对齐：仅平台管理员（非空间管理员）
  const canIntegrationRead = isSuper
  const canIntegrationWrite = isSuper
  const canAccessControl = canManageUsers || can(me, P.SYSTEM_ROLE_READ)

  const SPACE_MEMBER_ROLE_OPTS = [
    { value: 'admin', label: '空间管理员', title: '本空间全部离线能力 + 空间设置；不含平台系统管理' },
    { value: 'developer', label: '开发者', title: '开发/集成/运维等；不含空间设置与平台系统管理' },
    { value: 'viewer', label: '只读（分析师）', title: '仅数据探查 + 数据字典' },
  ]

  const [workspaceListUi, setWorkspaceListUi] = useState<any[]>([])
  const [wsManageId, setWsManageId] = useState<number | undefined>()
  const [wsMembersUi, setWsMembersUi] = useState<any[]>([])
  const [wsPanelLoading, setWsPanelLoading] = useState(false)
  const [wsMemberModalOpen, setWsMemberModalOpen] = useState(false)
  const [wsMemberSubmitting, setWsMemberSubmitting] = useState(false)
  const [wsMemberForm] = Form.useForm()
  const [ownerTransferForm] = Form.useForm()
  const [inviteCandidates, setInviteCandidates] = useState<{ id: number; username: string; email: string }[]>([])

  const loadWorkspaceMembers = async (wsId: number) => {
    const rows = (await workspaceApi.members(wsId)) as unknown as any[]
    setWsMembersUi(rows)
  }

  const refreshWorkspaceDropdown = async () => {
    if (!canWorkspacePlatformOps) return
    setWsPanelLoading(true)
    try {
      const list = (await workspaceApi.list()) as unknown as any[]
      setWorkspaceListUi(list)
    } finally {
      setWsPanelLoading(false)
    }
  }

  useEffect(() => {
    if (!canWorkspacePlatformOps) return
    refreshWorkspaceDropdown()
  }, [canWorkspacePlatformOps])

  useEffect(() => {
    if (!canManageWorkspaceMembersUi || canWorkspacePlatformOps) return
    if (!currentWorkspace?.id) return
    setWsManageId(currentWorkspace.id)
  }, [canManageWorkspaceMembersUi, canWorkspacePlatformOps, currentWorkspace?.id])

  useEffect(() => {
    if (!canWorkspacePlatformOps || !workspaceListUi.length) return
    setWsManageId(prev => {
      if (prev && workspaceListUi.some((w: any) => w.id === prev)) return prev
      return workspaceListUi[0].id
    })
  }, [canWorkspacePlatformOps, workspaceListUi])

  useEffect(() => {
    if (!wsManageId) return
    setWsPanelLoading(true)
    loadWorkspaceMembers(wsManageId).finally(() => setWsPanelLoading(false))
  }, [wsManageId])

  const load = async () => {
    setLoading(true)
    try {
      const [p, r, u] = await Promise.all([
        adminApi.listPermissions(),
        adminApi.listRoles(),
        adminApi.listUsers(),
      ]) as unknown as [PermRow[], RoleRow[], UserRow[]]
      setPerms(p)
      setRoles(r)
      setUsers(u)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (canAccessControl) load()
  }, [canAccessControl])

  useEffect(() => {
    if (!wsManageId || canAccessControl) {
      setInviteCandidates([])
      return
    }
    if (!canManageWorkspaceMembersUi) return
    let cancelled = false
    workspaceApi.inviteUserCandidates(wsManageId).then((rows: any) => {
      if (!cancelled) setInviteCandidates(rows || [])
    }).catch(() => {
      if (!cancelled) setInviteCandidates([])
    })
    return () => { cancelled = true }
  }, [wsManageId, canAccessControl, canManageWorkspaceMembersUi])

  const loadDolphin = async () => {
    if (!canIntegrationRead) return
    setDolphinLoading(true)
    try {
      const d: any = await adminApi.getDolphinIntegration()
      setDolphinMeta(d)
      dolphinForm.setFieldsValue({
        ds_enabled: d.override_enabled !== null && d.override_enabled !== undefined ? d.override_enabled : d.env_ds_enabled,
        ds_url: d.override_url ?? '',
        ds_ui_url: d.override_ui_url ?? '',
        ds_project_name: d.override_project_name ?? '',
        ds_token: '',
      })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载 Dolphin 配置失败')
    } finally {
      setDolphinLoading(false)
    }
  }

  useEffect(() => {
    if (canIntegrationRead) loadDolphin()
  }, [canIntegrationRead])

  const loadFlink = async () => {
    if (!canIntegrationRead) return
    setFlinkLoading(true)
    try {
      const f: any = await adminApi.getFlinkIntegration()
      setFlinkMeta(f)
      flinkForm.setFieldsValue({
        flink_url: f.override_flink_url ?? '',
        flink_sql_gateway_url: f.override_sql_gateway_url ?? '',
        flink_gateway_jobmanager_rest_url: f.override_gateway_jm_rest_url ?? '',
        flink_ui_url: f.override_ui_url ?? '',
        flink_k8s_application_image: f.override_flink_k8s_application_image ?? '',
        flink_k8s_namespace: f.override_flink_k8s_namespace ?? '',
        flink_k8s_application_jm_rest_template: f.override_flink_k8s_application_jm_rest_template ?? '',
        flink_k8s_cluster_domain: f.override_flink_k8s_cluster_domain ?? '',
        flink_k8s_apiserver_fallback_url: f.override_flink_k8s_apiserver_fallback_url ?? '',
        flink_k8s_jm_rpc_host: f.override_flink_k8s_jm_rpc_host ?? '',
        flink_k8s_sql_gateway_rest_host: f.override_flink_k8s_sql_gateway_rest_host ?? '',
      })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载 Flink 配置失败')
    } finally {
      setFlinkLoading(false)
    }
  }

  useEffect(() => {
    if (canIntegrationRead) loadFlink()
  }, [canIntegrationRead])

  const saveDolphin = async () => {
    const v = await dolphinForm.validateFields()
    try {
      const body: Record<string, unknown> = {
        ds_enabled: v.ds_enabled,
        ds_url: v.ds_url || null,
        ds_ui_url: v.ds_ui_url !== undefined && v.ds_ui_url !== '' ? v.ds_ui_url : null,
        ds_project_name: v.ds_project_name || null,
      }
      if (v.ds_token && String(v.ds_token).trim()) {
        body.ds_token = String(v.ds_token).trim()
      }
      await adminApi.putDolphinIntegration(body)
      message.success('已保存（Token 仅在有填写时更新）')
      await loadDolphin()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  const testDolphin = async () => {
    try {
      const r: any = await adminApi.testDolphinIntegration()
      message.success(r?.message || '连接正常')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '连接失败')
    }
  }

  const resetDolphin = async () => {
    try {
      await adminApi.resetDolphinIntegration()
      message.success('已清空库中覆盖项，现与环境变量一致')
      await loadDolphin()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重置失败')
    }
  }

  const saveFlink = async () => {
    const v = await flinkForm.validateFields()
    try {
      await adminApi.putFlinkIntegration({
        flink_url: v.flink_url || null,
        flink_sql_gateway_url: v.flink_sql_gateway_url || null,
        flink_gateway_jobmanager_rest_url: v.flink_gateway_jobmanager_rest_url || null,
        flink_ui_url: v.flink_ui_url || null,
        flink_k8s_application_image: v.flink_k8s_application_image || null,
        flink_k8s_namespace: v.flink_k8s_namespace || null,
        flink_k8s_application_jm_rest_template: v.flink_k8s_application_jm_rest_template || null,
        flink_k8s_cluster_domain: v.flink_k8s_cluster_domain || null,
        flink_k8s_apiserver_fallback_url: v.flink_k8s_apiserver_fallback_url || null,
        flink_k8s_jm_rpc_host: v.flink_k8s_jm_rpc_host || null,
        flink_k8s_sql_gateway_rest_host: v.flink_k8s_sql_gateway_rest_host || null,
      })
      message.success('Flink 配置已保存')
      await loadFlink()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  const testFlink = async () => {
    try {
      const r: any = await adminApi.testFlinkIntegration()
      if (r?.ok) {
        message.success('JobManager 与 SQL Gateway 探测均通过')
      } else {
        const jm = r?.jobmanager
        const gw = r?.sql_gateway
        const parts: string[] = []
        if (!jm?.reachable) parts.push(`JobManager: ${jm?.error || '不可达'}`)
        if (!gw?.ok) parts.push(`SQL Gateway: ${gw?.error || '不可达'}`)
        message.error(parts.join('；') || '探测未全部通过')
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '探测失败')
    }
  }

  const openDeployHint = async () => {
    try {
      const r: any = await adminApi.flinkDeployHint()
      setDeployHint(r)
      setDeployModal(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成失败')
    }
  }

  const exportSqlGatewayK8sYml = async () => {
    setFlinkLoading(true)
    try {
      const yml: string = await adminApi.flinkSqlGatewayK8sYml()
      const blob = new Blob([yml], { type: 'text/yaml;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = 'flink-sql-gateway-deployment.yaml'
      a.click()
      URL.revokeObjectURL(a.href)
      message.success('已下载 flink-sql-gateway-deployment.yaml')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '导出失败')
    } finally {
      setFlinkLoading(false)
    }
  }

  const resetFlink = async () => {
    try {
      await adminApi.resetFlinkIntegration()
      message.success('已清空 Flink 库覆盖项')
      await loadFlink()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重置失败')
    }
  }

  const permsByModule = useMemo(() => {
    const m: Record<string, PermRow[]> = {}
    for (const x of perms) {
      const k = x.module || '其他'
      if (!m[k]) m[k] = []
      m[k].push(x)
    }
    return m
  }, [perms])

  const openCreateUser = () => {
    userForm.resetFields()
    const dev = roles.find(r => r.code === 'developer')
    userForm.setFieldsValue({ role_id: dev?.id })
    setUserModal(true)
  }

  const saveNewUser = async () => {
    const v = await userForm.validateFields()
    try {
      await adminApi.createUser({
        username: v.username,
        email: v.email,
        password: v.password,
        full_name: v.full_name || undefined,
        role_id: v.role_id,
      })
      message.success('用户已创建，可将账号密码告知对方登录')
      setUserModal(false)
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const openWorkspaceMemberModal = () => {
    wsMemberForm.resetFields()
    wsMemberForm.setFieldsValue({ role: 'developer' })
    setWsMemberModalOpen(true)
  }

  const saveWorkspaceMember = async () => {
    const v = await wsMemberForm.validateFields()
    if (!wsManageId) return
    setWsMemberSubmitting(true)
    try {
      await workspaceApi.addMember(wsManageId, { user_id: v.user_id, role: v.role })
      message.success('已保存：该用户在所选空间内的角色（admin 即为空间管理员）')
      setWsMemberModalOpen(false)
      wsMemberForm.resetFields()
      await loadWorkspaceMembers(wsManageId)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setWsMemberSubmitting(false)
    }
  }

  const transferWorkspaceOwner = async () => {
    const v = await ownerTransferForm.validateFields()
    if (!wsManageId) return
    try {
      await workspaceApi.update(wsManageId, { owner_id: v.owner_id })
      message.success('负责人已变更，并已将其成员角色设为空间管理员(admin)')
      ownerTransferForm.resetFields()
      await refreshWorkspaceDropdown()
      await loadWorkspaceMembers(wsManageId)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '变更失败')
    }
  }

  const removeWorkspaceMemberClick = async (userId: number) => {
    if (!wsManageId) return
    try {
      await workspaceApi.removeMember(wsManageId, userId)
      message.success('已移出当前工作空间')
      await loadWorkspaceMembers(wsManageId)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '移除失败')
    }
  }

  const openCreateRole = () => {
    setEditingRole(null)
    form.resetFields()
    setPermPick([])
    setRoleModal(true)
  }

  const openEditRole = (r: RoleRow) => {
    setEditingRole(r)
    form.setFieldsValue({ name: r.name, description: r.description })
    setPermPick(r.permission_codes || [])
    setRoleModal(true)
  }

  const saveRole = async () => {
    const v = await form.validateFields()
    try {
      if (editingRole) {
        if (editingRole.is_system) {
          await adminApi.updateRole(editingRole.id, { name: v.name, description: v.description })
        } else {
          await adminApi.updateRole(editingRole.id, {
            name: v.name,
            description: v.description,
            permission_codes: permPick,
          })
        }
        message.success('已保存')
      } else {
        await adminApi.createRole({
          code: v.code,
          name: v.name,
          description: v.description,
          permission_codes: permPick,
        })
        message.success('已创建角色')
      }
      setRoleModal(false)
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  const userColumns = [
    { title: '用户', dataIndex: 'username', key: 'username' },
    { title: '邮箱', dataIndex: 'email', key: 'email', ellipsis: true },
    {
      title: '平台角色',
      key: 'role',
      render: (_: any, row: UserRow) => (
        <Select
          style={{ minWidth: 160 }}
          value={row.role_id}
          options={roles.map(r => ({ label: `${r.name} (${r.code})`, value: r.id }))}
          disabled={!can(me, P.SYSTEM_USER_WRITE)}
          onChange={async (role_id: number) => {
            try {
              await adminApi.setUserRole(row.id, role_id)
              message.success('角色已更新')
              await load()
              if (row.id === me?.id) {
                const u: any = await authApi.me()
                setUser(u)
              }
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '更新失败')
            }
          }}
        />
      ),
    },
    {
      title: '管理员',
      key: 'is_admin',
      width: 90,
      render: (_: any, row: UserRow) => (
        <Switch
          checked={row.is_admin}
          disabled={!isSuper || row.id === me?.id}
          onChange={async (checked) => {
            try {
              await adminApi.setUserFlags(row.id, { is_admin: checked })
              message.success('已更新')
              await load()
              if (row.id === me?.id) {
                const u: any = await authApi.me()
                setUser(u)
              }
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '仅超级管理员可修改')
            }
          }}
        />
      ),
    },
    {
      title: '启用',
      key: 'is_active',
      width: 90,
      render: (_: any, row: UserRow) => (
        <Switch
          checked={row.is_active}
          disabled={!isSuper || row.id === me?.id}
          onChange={async (checked) => {
            try {
              await adminApi.setUserFlags(row.id, { is_active: checked })
              message.success('已更新')
              await load()
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '更新失败')
            }
          }}
        />
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 80,
      render: (_: any, row: UserRow) => (
        canDeleteUser && row.id !== me?.id ? (
          <Popconfirm title="删除该用户？" onConfirm={async () => {
            try {
              await adminApi.deleteUser(row.id)
              message.success('已删除')
              await load()
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '删除失败')
            }
          }}>
            <Button type="link" danger size="small">删除</Button>
          </Popconfirm>
        ) : null
      ),
    },
  ]

  const roleColumns = [
    { title: 'Code', dataIndex: 'code', key: 'code', width: 140 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '类型',
      key: 'sys',
      width: 100,
      render: (_: any, r: RoleRow) => r.is_system ? <Tag color="blue">内置</Tag> : <Tag>自定义</Tag>,
    },
    { title: '权限数', key: 'pc', width: 90, render: (_: any, r: RoleRow) => r.permission_codes?.length ?? 0 },
    {
      title: '操作',
      key: 'op',
      width: 160,
      render: (_: any, r: RoleRow) => (
        <Space>
          {canWriteRole && (
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditRole(r)}>编辑</Button>
          )}
          {canDeleteRole && !r.is_system && (
            <Popconfirm title="删除角色？" onConfirm={async () => {
              try {
                await adminApi.deleteRole(r.id)
                message.success('已删除')
                await load()
              } catch (e: any) {
                message.error(e?.response?.data?.detail || '删除失败')
              }
            }}>
              <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  if (!canAccessControl && !canIntegrationRead && !canManageWorkspaceMembersUi) {
    return <Card>无权访问系统管理</Card>
  }

  const dolphinPanel = (
    <div style={{ maxWidth: 720 }}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="全局 Dolphin 配置（回退项）"
        description={
          '各工作空间可在侧栏「空间设置」配置独立 Dolphin，用于区分测试与生产；此处为空间未覆盖时的全局默认。'
          + ' 默认 / .env 只决定后端连哪台 Dolphin API，不会替你启动 DolphinScheduler。'
          + ' 留空 URL/项目名表示沿用环境变量；Token 不填则不修改库中已存 Token。'
        }
      />
      <Descriptions size="small" bordered column={1} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="当前生效">
          {dolphinMeta?.effective_enabled ? '已启用' : '未启用'} · API {dolphinMeta?.effective_url || '—'}
          {dolphinMeta?.effective_url_source === 'database'
            ? '（地址来自库覆盖）'
            : '（地址来自环境变量）'}
        </Descriptions.Item>
        <Descriptions.Item label="环境变量基线">
          DS_ENABLED={dolphinMeta?.env_ds_enabled ? 'true' : 'false'} · {dolphinMeta?.env_ds_url || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="库中 Token">
          {dolphinMeta?.token_configured_in_db
            ? (dolphinMeta?.token_masked || '已配置')
            : '未在库中配置（沿用环境变量）'}
        </Descriptions.Item>
      </Descriptions>
      <Form form={dolphinForm} layout="vertical" disabled={!canIntegrationWrite}>
        <Form.Item name="ds_enabled" label="启用 Dolphin" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item name="ds_url" label="DS API 根路径（可选覆盖）" tooltip="例：http://dolphinscheduler-api:12345/dolphinscheduler">
          <Input placeholder="留空则使用环境变量 DS_URL" />
        </Form.Item>
        <Form.Item name="ds_ui_url" label="DS Web UI 根路径（可选）" tooltip="浏览器打开工作流定义用；留空则 effective_url + /ui">
          <Input placeholder="可选" />
        </Form.Item>
        <Form.Item name="ds_project_name" label="DS 项目名称（可选覆盖）">
          <Input placeholder="默认 GIDO" />
        </Form.Item>
        <Form.Item
          name="ds_token"
          label="DS API Token（可选）"
          tooltip="海豚 安全中心 → 令牌管理。仅保存时填写才会更新；留空表示保持原样"
        >
          <Input.Password placeholder="不修改请留空" autoComplete="off" />
        </Form.Item>
      </Form>
      <Space wrap>
        {canIntegrationWrite && (
          <Button type="primary" icon={<ApiOutlined />} onClick={saveDolphin} loading={dolphinLoading}>
            保存配置
          </Button>
        )}
        <Button icon={<ExperimentOutlined />} onClick={testDolphin} loading={dolphinLoading}>
          测试连接
        </Button>
        {canIntegrationWrite && (
          <Popconfirm title="确定清空库中所有 Dolphin 覆盖项？" onConfirm={resetDolphin}>
            <Button danger loading={dolphinLoading}>清空覆盖（回退 .env）</Button>
          </Popconfirm>
        )}
        <Button onClick={loadDolphin} loading={dolphinLoading}>刷新</Button>
      </Space>
    </div>
  )

  const flinkPanel = (
    <div style={{ maxWidth: 1280 }}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="可插拔对接 Flink（Kubernetes Session + 可选 Application）"
        description={
          'Flink Session 请用仓库根 k8s/legacy/flink.yaml 部署；此处配置写入数据库后会覆盖环境变量 FLINK_* / FLINK_K8S_*（REST、K8s Application、集群域与 SQL Gateway 清单相关项），后端即时生效。'
          + ' 「生成部署变量」可导出与当前生效值一致的 GIDO_* 片段；「导出 SQL Gateway Deployment YAML」按下方 K8s 可插拔项生成与集群对齐的 flink-sql-gateway Deployment，便于对接不同 K8s。'
        }
      />
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="与「实时 → Flink 集群连接」的关系（避免重复维护）"
        description={(
          <span>
            <strong>本页 = 租户级默认</strong>（全工作空间、未选作业连接时的合并基线）。若同一 Flink 集群被多数作业共用，只在此处配置即可。
            <strong> 工作空间「Flink 集群连接」</strong>用于<strong>多套物理集群</strong>（风控 / 对账等）：仅填写与默认<strong>不同</strong>的字段，在默认之上覆写，不形成第二套真相源。
            详见 <Link to={R.stream.flinkSessions}>实时 → Flink 集群连接</Link> 页顶说明。
          </span>
        )}
      />
      <Row gutter={[24, 24]}>
        <Col xs={24} lg={12}>
          <Title level={5} style={{ marginTop: 0, marginBottom: 12 }}>Session / REST 与浏览器</Title>
          <Descriptions size="small" bordered column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="JobManager REST">
              {flinkMeta?.effective_flink_url || '—'}
              {flinkMeta?.effective_flink_url_source === 'database' ? '（库覆盖）' : '（环境变量）'}
            </Descriptions.Item>
            <Descriptions.Item label="SQL Gateway">{flinkMeta?.effective_sql_gateway_url || '—'}</Descriptions.Item>
            <Descriptions.Item label="Gateway→JM 覆盖">{flinkMeta?.effective_gateway_jm_rest_url || '—'}</Descriptions.Item>
            <Descriptions.Item label="Flink Web UI">{flinkMeta?.effective_ui_url || '—'}</Descriptions.Item>
            <Descriptions.Item label="环境变量基线（Session）">
              JM {flinkMeta?.env_flink_url || '—'} · Gateway {flinkMeta?.env_sql_gateway_url || '—'}
            </Descriptions.Item>
          </Descriptions>
        </Col>
        <Col xs={24} lg={12}>
          <Title level={5} style={{ marginTop: 0, marginBottom: 12 }}>K8s Application 与集群内 SQL Gateway</Title>
          <Descriptions size="small" bordered column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="K8s Application 镜像">
              {flinkMeta?.effective_flink_k8s_application_image || '—'}
              {flinkMeta?.effective_flink_k8s_image_source === 'database' ? '（库覆盖）' : '（环境变量）'}
            </Descriptions.Item>
            <Descriptions.Item label="K8s 命名空间（Application）">{flinkMeta?.effective_flink_k8s_namespace || '—'}</Descriptions.Item>
            <Descriptions.Item label={'JM REST 模板（Application，含 {cluster_id}）'}>{flinkMeta?.effective_flink_k8s_application_jm_rest_template || '—'}</Descriptions.Item>
            <Descriptions.Item label="K8s 集群域">{flinkMeta?.effective_flink_k8s_cluster_domain || '—'}</Descriptions.Item>
            <Descriptions.Item label="apiserver 回退 URL">{flinkMeta?.effective_flink_k8s_apiserver_fallback_url || '—'}</Descriptions.Item>
            <Descriptions.Item label="JM RPC 主机（Gateway JVM -D）">{flinkMeta?.effective_flink_k8s_jm_rpc_host || '—'}</Descriptions.Item>
            <Descriptions.Item label="SQL Gateway REST 广告主机">{flinkMeta?.effective_flink_k8s_sql_gateway_rest_host || '—'}</Descriptions.Item>
            <Descriptions.Item label="环境变量基线（K8s）">
              镜像 {flinkMeta?.env_flink_k8s_application_image || '—'}
              {' · 集群域 '}{flinkMeta?.env_flink_k8s_cluster_domain || '—'}
            </Descriptions.Item>
          </Descriptions>
        </Col>
      </Row>
      <Form form={flinkForm} layout="vertical" disabled={!canIntegrationWrite} style={{ marginTop: 8 }}>
        <Row gutter={[24, 8]}>
          <Col xs={24} lg={12}>
            <Title level={5} style={{ marginBottom: 12 }}>平台默认（库覆盖项）</Title>
            <Form.Item name="flink_url" label="FLINK_URL（JobManager REST）" tooltip="K8s LoadBalancer/Ingress/port-forward 可达的 JM REST，如 http://127.0.0.1:8081">
              <Input placeholder="留空则使用环境变量" />
            </Form.Item>
            <Form.Item
              name="flink_sql_gateway_url"
              label="FLINK_SQL_GATEWAY_URL"
              tooltip="Gateway 的 /v1 根地址（常见 :8083）；集群内 DNS 或 Ingress/NodePort 映射到后端的 URL"
            >
              <Input placeholder="留空则使用环境变量" />
            </Form.Item>
            <Form.Item
              name="flink_gateway_jobmanager_rest_url"
              label="FLINK_GATEWAY_JOBMANAGER_REST_URL（可选）"
              tooltip="Gateway 容器内能访问的 JM 地址；不填则用 FLINK_URL"
            >
              <Input placeholder="可选" />
            </Form.Item>
            <Form.Item name="flink_ui_url" label="FLINK_UI_URL（浏览器打开作业页）" tooltip="不填则常用 JM 同 host">
              <Input placeholder="可选" />
            </Form.Item>
          </Col>
          <Col xs={24} lg={12}>
            <Title level={5} style={{ marginBottom: 12 }}>Application / K8s 配置</Title>
            <Form.Item
              name="flink_k8s_application_image"
              label="K8s Application 作业镜像"
              tooltip="如 apache/flink:2.0.1-java11；留空则使用环境变量 FLINK_K8S_APPLICATION_IMAGE"
            >
              <Input placeholder="可选覆盖" />
            </Form.Item>
            <Form.Item name="flink_k8s_namespace" label="K8s 命名空间（Application）" tooltip="留空则使用 FLINK_K8S_NAMESPACE，再默认可为 flink">
              <Input placeholder="可选，如 flink" />
            </Form.Item>
            <Form.Item
              name="flink_k8s_application_jm_rest_template"
              label="Application JM REST 模板"
              tooltip="须含 {cluster_id}，如 http://{cluster_id}-rest.flink.svc.cluster.local:8081；留空则尝试 kubeconfig NodePort"
            >
              <Input placeholder="可选" />
            </Form.Item>
            <Title level={5} style={{ margin: '16px 0 12px' }}>集群内 SQL Gateway（与 kubectl 清单同步）</Title>
            <Form.Item
              name="flink_k8s_cluster_domain"
              label="K8s 集群 DNS 域"
              tooltip="多数集群为 cluster.local；OpenShift 等可能不同。留空则使用 FLINK_K8S_CLUSTER_DOMAIN 或默认 cluster.local"
            >
              <Input placeholder="如 cluster.local" />
            </Form.Item>
            <Form.Item
              name="flink_k8s_apiserver_fallback_url"
              label="apiserver 回退 URL（可选）"
              tooltip="init 写 kubeconfig 时若 Pod 内无 KUBERNETES_SERVICE_HOST 则使用该地址；须含 https:// 与端口，如 https://kubernetes.default.svc.cluster.local:443"
            >
              <Input placeholder="可选，一般留空由集群域推导" />
            </Form.Item>
            <Form.Item
              name="flink_k8s_jm_rpc_host"
              label="JM RPC 主机（可选）"
              tooltip="覆盖 -Djobmanager.rpc.address；留空则为 flink-jobmanager.<命名空间>.svc.<集群域>"
            >
              <Input placeholder="可选" />
            </Form.Item>
            <Form.Item
              name="flink_k8s_sql_gateway_rest_host"
              label="SQL Gateway REST 广告主机（可选）"
              tooltip="覆盖 -Dsql-gateway.endpoint.rest.address；留空则为 flink-sql-gateway.<命名空间>.svc.<集群域>"
            >
              <Input placeholder="可选" />
            </Form.Item>
          </Col>
        </Row>
      </Form>
      <Space wrap>
        {canIntegrationWrite && (
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={saveFlink} loading={flinkLoading}>
            保存并应用
          </Button>
        )}
        <Button icon={<ExperimentOutlined />} onClick={testFlink} loading={flinkLoading}>
          探测连通性
        </Button>
        <Button icon={<FileTextOutlined />} onClick={openDeployHint} loading={flinkLoading}>
          生成部署变量
        </Button>
        <Button icon={<DownloadOutlined />} onClick={exportSqlGatewayK8sYml} loading={flinkLoading}>
          导出 SQL Gateway Deployment YAML
        </Button>
        {canIntegrationWrite && (
          <Popconfirm title="确定清空 Flink 库覆盖项？" onConfirm={resetFlink}>
            <Button danger loading={flinkLoading}>清空覆盖</Button>
          </Popconfirm>
        )}
        <Button onClick={loadFlink} loading={flinkLoading}>刷新</Button>
      </Space>
    </div>
  )

  if (view === 'integration') {
    if (!canIntegrationRead) {
      return <Card>无权访问平台集成</Card>
    }
    return (
      <div style={{ maxWidth: 1280 }}>
        <Tabs
          items={[
            { key: 'dolphin', label: 'DolphinScheduler', children: dolphinPanel },
            { key: 'flink', label: 'Apache Flink', children: flinkPanel },
          ]}
        />
      </div>
    )
  }

  const topTabItems: any[] = []
  if (canAccessControl) {
    topTabItems.push({
      key: 'users',
      label: '用户管理',
      children: (
        <div style={{ maxWidth: 1100 }}>
          <Space style={{ marginBottom: 14 }} wrap>
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
              刷新列表
            </Button>
            {canCreateUser && (
              <Button type="primary" icon={<UserAddOutlined />} onClick={openCreateUser}>
                新建用户
              </Button>
            )}
          </Space>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 14 }}
            message="平台账号"
            description="列表为可登录 GIDO 的账号。登录后的能力由「平台角色」聚合；空间内的 admin/developer/viewer 在「工作空间成员」中单独配置。"
          />
          {canManageUsers ? (
            <Table rowKey="id" loading={loading} dataSource={users} columns={userColumns} pagination={{ pageSize: 12 }} />
          ) : (
            <Card>需要 system:user:read 权限</Card>
          )}
        </div>
      ),
    })
    topTabItems.push({
      key: 'roles',
      label: '角色与权限',
      children: (
        <div style={{ maxWidth: 1100 }}>
          <Space style={{ marginBottom: 14 }} wrap>
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
              刷新列表
            </Button>
            {canWriteRole && (
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreateRole}>
                新建自定义角色
              </Button>
            )}
          </Space>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 14 }}
            message="平台角色"
            description="内置角色一般不可改权限集合；自定义角色用于为同事分配一组权限码。用户与角色的绑定在「用户管理」中完成。"
          />
          {can(me, P.SYSTEM_ROLE_READ) ? (
            <Table rowKey="id" loading={loading} dataSource={roles} columns={roleColumns} pagination={{ pageSize: 12 }} />
          ) : (
            <Card>需要 system:role:read 权限</Card>
          )}
        </div>
      ),
    })
  }
  if (canManageWorkspaceMembersUi) {
    topTabItems.push({
      key: 'workspace_ops',
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <TeamOutlined />
          工作空间成员
        </span>
      ),
      children: (
        <div style={{ maxWidth: 980 }}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={
              canWorkspacePlatformOps
                ? '平台管理员：可管理任意工作空间的成员与负责人'
                : '空间管理员：仅管理当前工作空间内的成员角色'
            }
            description={
              <>
                此处<strong>只配置</strong>用户在各工作空间内的 <Tag>admin</Tag> / <Tag>developer</Tag> / <Tag>viewer</Tag>，<strong>不修改</strong>用户的平台角色。
                {!canWorkspacePlatformOps && (
                  <> 创建空间、Dolphin/Flink 等请在侧栏「平台集成」或顶栏联系平台管理员。</>
                )}
                {canWorkspacePlatformOps && (
                  <> Dolphin/Flink 等在侧栏「系统管理 → 平台集成」；新建空间在顶栏；负责人 (owner) 变更见下方。</>
                )}
              </>
            }
          />
          <Space wrap style={{ marginBottom: 14 }}>
            <span style={{ color: '#666' }}>{canWorkspacePlatformOps ? '管理范围' : '当前空间'}</span>
            {canWorkspacePlatformOps ? (
              <Select<number>
                style={{ minWidth: 260 }}
                loading={wsPanelLoading}
                value={wsManageId}
                options={workspaceListUi.map((w: any) => ({
                  label: `${w.name} (id=${w.id})`,
                  value: w.id,
                }))}
                placeholder="请先刷新列表"
                onChange={setWsManageId}
              />
            ) : (
              <span style={{ fontWeight: 500 }}>
                {currentWorkspace?.name || '—'}
                <span style={{ color: '#94a3b8', marginLeft: 8 }}>id={currentWorkspace?.id ?? '—'}</span>
              </span>
            )}
            {canWorkspacePlatformOps && (
              <Button
                icon={<ReloadOutlined />}
                loading={wsPanelLoading}
                onClick={() => refreshWorkspaceDropdown().then(async () => { if (wsManageId) await loadWorkspaceMembers(wsManageId) })}
              >
                刷新空间列表
              </Button>
            )}
            {!canWorkspacePlatformOps && wsManageId && (
              <Button icon={<ReloadOutlined />} loading={wsPanelLoading} onClick={() => loadWorkspaceMembers(wsManageId)}>
                刷新成员
              </Button>
            )}
            <Button type="primary" icon={<UserAddOutlined />} onClick={openWorkspaceMemberModal}>
              添加 / 调整成员
            </Button>
          </Space>

          <Table
            loading={wsPanelLoading}
            rowKey="user_id"
            dataSource={wsMembersUi}
            pagination={{ pageSize: 12 }}
            columns={[
              {
                title: '用户',
                key: 'u',
                render: (_: unknown, row: any) => (
                  <Space>
                    <span>{row.username || `id=${row.user_id}`}</span>
                    {row.is_owner ? <Tag color="blue">负责人</Tag> : null}
                  </Space>
                ),
              },
              { title: '空间内角色', dataIndex: 'role', width: 120 },
              {
                title: '操作',
                width: 100,
                render: (_: unknown, row: any) => (
                  row.is_owner ? (
                    <span style={{ color: '#999' }}>—</span>
                  ) : (
                    <Popconfirm title="将该用户移出当前工作空间？" onConfirm={() => removeWorkspaceMemberClick(row.user_id)}>
                      <Button type="link" danger size="small" icon={<DeleteOutlined />}>移除</Button>
                    </Popconfirm>
                  )
                ),
              },
            ]}
          />

          {canWorkspacePlatformOps && (
            <>
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 20 }}
                message="变更负责人（owner）"
                description="不能直接移除现任负责人；请先在下方选择新任负责人并转移，系统会将其设为空间 admin。"
              />
              <Form layout="inline" form={ownerTransferForm} style={{ marginTop: 12 }} onFinish={transferWorkspaceOwner}>
                <Form.Item name="owner_id" label="新任负责人" rules={[{ required: true, message: '请选择用户 id' }]}>
                  <Select<number>
                    showSearch
                    style={{ width: 360 }}
                    placeholder="选择平台上的用户账号"
                    optionFilterProp="label"
                    options={users.map(u => ({
                      value: u.id,
                      label: `${u.username} (id=${u.id})`,
                    }))}
                  />
                </Form.Item>
                <Form.Item>
                  <Button type="primary" htmlType="submit">
                    转移负责人并设为空间 admin
                  </Button>
                </Form.Item>
              </Form>
            </>
          )}
        </div>
      ),
    })
  }
  /** 仅当没有任何「成员与权限」子页可展示、又需要集成入口时保留（极少见） */
  const hasOtherSystemTabs = canAccessControl || canManageWorkspaceMembersUi
  if (canIntegrationRead && !hasOtherSystemTabs) {
    topTabItems.push({
      key: 'integrations',
      label: '平台集成',
      children: (
        <Tabs
          items={[
            { key: 'dolphin', label: 'DolphinScheduler', children: dolphinPanel },
            { key: 'flink', label: 'Apache Flink', children: flinkPanel },
          ]}
        />
      ),
    })
  }

  const defaultSystemTab = canManageUsers
    ? 'users'
    : (can(me, P.SYSTEM_ROLE_READ) ? 'roles' : (canManageWorkspaceMembersUi ? 'workspace_ops' : 'integrations'))

  return (
    <div>
      {(canAccessControl || canManageWorkspaceMembersUi) && (
        <div style={{ marginBottom: 20 }}>
          <Title level={4} style={{ marginBottom: 6 }}>成员与权限</Title>
          <Text type="secondary">管理平台账号、平台角色与各工作空间成员；与「平台集成」解耦。</Text>
        </div>
      )}

      <Tabs
        defaultActiveKey={defaultSystemTab}
        items={topTabItems}
      />

      <Modal
        title="Flink — 部署环境变量（与当前生效配置一致）"
        open={deployModal}
        onCancel={() => setDeployModal(false)}
        footer={null}
        width={640}
      >
        <p style={{ color: '#666', fontSize: 13 }}>{deployHint?.note}</p>
        <div style={{ marginBottom: 8, fontWeight: 600 }}>.env / 单行</div>
        <Input.TextArea
          readOnly
          rows={5}
          value={(deployHint?.env_lines || []).join('\n')}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />
        <div style={{ margin: '12px 0 8px', fontWeight: 600 }}>docker-compose 片段</div>
        <Input.TextArea
          readOnly
          rows={6}
          value={deployHint?.compose_snippet || ''}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />
      </Modal>

      <Modal
        title="工作空间 · 添加 / 变更成员的角色"
        open={wsMemberModalOpen}
        onOk={saveWorkspaceMember}
        onCancel={() => { setWsMemberModalOpen(false); wsMemberForm.resetFields() }}
        confirmLoading={wsMemberSubmitting}
        destroyOnClose
        okText="保存"
        width={480}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 14 }}
          message={
            canAccessControl
              ? '选择「admin」可将该同事设为所选工作空间的<strong>空间管理员</strong>'
              : '仅从「尚未加入本空间的平台用户」中选人；平台级角色与全局权限不在此修改。选择「admin」即本空间的空间管理员。'
          }
        />
        <Form form={wsMemberForm} layout="vertical">
          <Form.Item name="user_id" label="用户" rules={[{ required: true, message: '请选择用户' }]}>
            <Select<number>
              showSearch
              optionFilterProp="label"
              placeholder={canAccessControl ? '平台用户列表' : '可邀请加入本空间的用户'}
              options={(canAccessControl ? users : inviteCandidates).map((u: any) => ({
                value: u.id,
                label: `${u.username}${u.email ? ` (${u.email})` : ''}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="role" label="在该空间内的角色" rules={[{ required: true }]} initialValue="developer">
            <Select options={SPACE_MEMBER_ROLE_OPTS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建用户"
        open={userModal}
        onOk={saveNewUser}
        onCancel={() => setUserModal(false)}
        destroyOnClose
        width={480}
      >
        <Form form={userForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}>
            <Input placeholder="登录名，唯一" autoComplete="off" />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="唯一" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}>
            <Input.Password placeholder="至少 6 位，请告知对方首次登录后修改" />
          </Form.Item>
          <Form.Item name="full_name" label="显示名">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="role_id" label="平台角色" rules={[{ required: true, message: '请选择角色' }]}>
            <Select
              placeholder="决定该用户有哪些菜单与操作权限"
              options={roles.map(r => ({ label: `${r.name} (${r.code})`, value: r.id }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingRole ? `编辑角色 — ${editingRole.code}` : '新建自定义角色'}
        open={roleModal}
        onOk={saveRole}
        onCancel={() => setRoleModal(false)}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          {!editingRole && (
            <Form.Item name="code" label="角色 Code（小写+下划线）" rules={[{ required: true }]}>
              <Input placeholder="例如 data_analyst" />
            </Form.Item>
          )}
          <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
        {editingRole?.is_system && (
          <div style={{ color: '#999', marginBottom: 12 }}>内置角色的权限集合不可改；仅可调整名称与说明。</div>
        )}
        {(!editingRole || !editingRole.is_system) && (
          <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 8, padding: 12 }}>
            {Object.entries(permsByModule).map(([mod, list]) => (
              <div key={mod} style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>{mod}</div>
                <Checkbox.Group
                  value={permPick}
                  onChange={v => setPermPick(v as string[])}
                  style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                >
                  {list.map(p => (
                    <Checkbox key={p.code} value={p.code}>{p.name} <span style={{ color: '#999' }}>({p.code})</span></Checkbox>
                  ))}
                </Checkbox.Group>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
