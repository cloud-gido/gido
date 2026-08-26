/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 节点配置弹窗（数据开发 / 工作流 DAG 共用）。
 * 保存走同一 studio API；协作编辑锁与数据开发共享。
 */
import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { Modal, Form, Input, Select, Tag, Button, Space, message, Spin, Radio, Card, Tabs, Descriptions } from 'antd'
import { LockOutlined, PlusOutlined, DeleteOutlined } from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { studioApi, datasourceApi, integrationApi, workflowApi } from '../api'
import { useAppStore } from '../store'
import { resolveDatasourceForRun } from '../utils/workspaceDatasource'
import {
  DEPENDENT_DATE_OPTIONS,
  dependentFormToParams,
  dependentParamsToForm,
} from '../utils/dependentParams'
import {
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  registerDwMonacoThemes,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from './MonacoFindBar'
import AutosaveStatusHint from './AutosaveStatusHint'
import { useScriptAutosave } from '../hooks/useScriptAutosave'
import {
  restoreScriptLocalDraft,
  scriptDraftStorageKey,
} from '../utils/scriptLocalDraft'
import { Z_NODE_CONFIG, Z_NODE_CONFIG_CONFIRM } from './dagEditorOverlay'

export type StudioNode = Record<string, any>

const SCRIPT_LANG: Record<string, string> = {
  SQL: 'sql',
  PYTHON: 'python',
  SHELL: 'shell',
}

const SCRIPT_NODE_TYPES = new Set(['SQL', 'PYTHON', 'SHELL'])

interface NodeConfigModalProps {
  open: boolean
  nodeId: number | null
  workspaceId: number
  /** 关闭时是否释放本会话占用的编辑锁（工作流侧建议 true；Studio 已占锁时 false） */
  releaseOnClose?: boolean
  /** 平台/空间写权限；无写权限时静默只读，不抢锁、不因打开弹窗弹 403 */
  canWrite?: boolean
  onClose: () => void
  onSaved?: (node: StudioNode) => void
  /** 可选：由 Studio 注入，与页面内锁状态保持一致 */
  ensureEditLock?: (opts?: { silent?: boolean }) => Promise<boolean>
}

function normalizeFormValues(node: StudioNode) {
  const vals: any = { ...node }
  vals.script_content = node.script_content ?? ''
  const p = vals.params
  if (p == null || p === '') {
    vals.params = ''
  } else if (typeof p === 'object' && !Array.isArray(p)) {
    vals.params = JSON.stringify(p, null, 2)
  } else if (typeof p === 'string') {
    vals.params = p
  } else {
    vals.params = String(p)
  }
  if (vals.node_type === 'SYNC') {
    let syncId = null
    if (typeof node.params === 'object' && node.params && !Array.isArray(node.params)) {
      syncId = node.params.sync_task_id
    } else if (typeof vals.params === 'string') {
      try { syncId = JSON.parse(vals.params).sync_task_id } catch { /* ignore */ }
    }
    vals.sync_task_id = syncId
  }
  if (vals.node_type === 'DEPENDENT') {
    const depForm = dependentParamsToForm(node.params)
    vals.relation = depForm.relation
    vals.depend_items = depForm.depend_items
  }
  return vals
}

export default function NodeConfigModal({
  open,
  nodeId,
  workspaceId,
  releaseOnClose = true,
  canWrite = true,
  onClose,
  onSaved,
  ensureEditLock,
}: NodeConfigModalProps) {
  const currentWorkspace = useAppStore(s => s.currentWorkspace)
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [node, setNode] = useState<StudioNode | null>(null)
  const [datasources, setDatasources] = useState<any[]>([])
  const [integrationTasks, setIntegrationTasks] = useState<any[]>([])
  const [workflows, setWorkflows] = useState<any[]>([])
  const [holdsLock, setHoldsLock] = useState(false)
  const [scriptDirty, setScriptDirty] = useState(false)
  const holdsLockRef = useRef(false)
  holdsLockRef.current = holdsLock
  const acquiredHereRef = useRef(false)
  const nodeRef = useRef<StudioNode | null>(null)
  const editorRef = useRef<any>(null)
  const findApiRef = useRef<MonacoFindBarApi | null>(null)

  const dsResolve = node && (node.node_type === 'SQL' || node.node_type === 'PYTHON')
    ? resolveDatasourceForRun(node.datasource_id, currentWorkspace, datasources)
    : null

  const editorAppearance = useMemo(() => loadEditorAppearance(), [])
  const showScriptEditor = Boolean(node && SCRIPT_NODE_TYPES.has(node.node_type))
  const scriptContent = Form.useWatch('script_content', form) ?? ''
  nodeRef.current = node
  const syncTaskId = Form.useWatch('sync_task_id', form)
  const dependRelation = Form.useWatch('relation', form)
  const dependItems = Form.useWatch('depend_items', form)

  const selectedSyncTask = useMemo(
    () => integrationTasks.find((t: any) => t.id === syncTaskId) || null,
    [integrationTasks, syncTaskId],
  )

  const dependentPreview = useMemo(() => {
    const items = Array.isArray(dependItems) && dependItems.length
      ? dependItems
      : [{ depend_workflow_id: null, date_value: 'today' }]
    return {
      relation: dependRelation || 'AND',
      depend_items: items.map((it: any) => ({
        depend_workflow_id: it?.depend_workflow_id ?? null,
        depend_workflow_name: workflows.find((w: any) => w.id === it?.depend_workflow_id)?.name || null,
        cycle: it?.cycle || 'day',
        date_value: it?.date_value || 'today',
      })),
    }
  }, [dependRelation, dependItems, workflows])

  const primaryTabLabel = node?.node_type === 'SYNC'
    ? '同步任务'
    : node?.node_type === 'DEPENDENT'
      ? '依赖配置'
      : '脚本'

  const refreshNode = useCallback(async () => {
    if (!nodeId) return null
    const n: any = await studioApi.getNode(nodeId)
    setNode(n)
    form.setFieldsValue(normalizeFormValues(n))
    return n as StudioNode
  }, [nodeId, form])

  const tryAcquire = useCallback(async (force = false, silent = false): Promise<boolean> => {
    if (!nodeId) return false
    // 只读角色：不抢锁、不打 acquire API（避免运维打开配置就 403 toast）
    if (!canWrite) return false
    if (ensureEditLock && !force) {
      const ok = await ensureEditLock({ silent })
      if (ok) {
        setHoldsLock(true)
        return true
      }
      // 父级已判定失败（无权限 / 409 等），不再二次请求
      return false
    }
    try {
      const res: any = await studioApi.acquireEditLock(nodeId, force || undefined)
      setNode(res.node)
      setHoldsLock(true)
      acquiredHereRef.current = true
      return true
    } catch (e: any) {
      setHoldsLock(false)
      if (!silent) {
        if (e?.response?.status === 409) {
          message.warning(e?.response?.data?.detail || '节点正由他人编辑')
        } else if (e?.response?.status === 403) {
          message.error(e?.response?.data?.detail || '无节点编辑权限')
        } else if (e?.response?.status !== 401) {
          message.error(e?.response?.data?.detail || '无法获取编辑锁')
        }
      }
      return false
    }
  }, [nodeId, ensureEditLock, canWrite])

  useEffect(() => {
    if (!open || !nodeId || !workspaceId) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      acquiredHereRef.current = false
      setHoldsLock(false)
      try {
        const [n, ds, tasks, wfs]: any = await Promise.all([
          studioApi.getNode(nodeId),
          datasourceApi.list(workspaceId),
          integrationApi.listTasks(workspaceId).catch(() => []),
          workflowApi.listAll(workspaceId).catch(() => ({ items: [] })),
        ])
        if (cancelled) return
        setNode(n)
        setDatasources(ds || [])
        setIntegrationTasks(Array.isArray(tasks) ? tasks : (tasks?.items || []))
        setWorkflows(Array.isArray(wfs?.items) ? wfs.items : (Array.isArray(wfs) ? wfs : []))
        const vals = normalizeFormValues(n)
        const draftKey = scriptDraftStorageKey(`studio.${workspaceId}`, nodeId)
        const restored = canWrite && !n.is_locked
          ? restoreScriptLocalDraft(draftKey, n.script_content ?? '')
          : null
        if (restored != null) {
          vals.script_content = restored
          setScriptDirty(true)
          message.info('已恢复本地未同步草稿，持有编辑锁后将自动保存到服务端')
        } else {
          setScriptDirty(false)
        }
        form.setFieldsValue(vals)
        if (canWrite && !n.is_locked) {
          await tryAcquire(false, true)
        }
      } catch (e: any) {
        if (!cancelled) message.error(e?.response?.data?.detail || '加载节点失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [open, nodeId, workspaceId, form, tryAcquire, canWrite])

  const modalDraftKey = nodeId != null
    ? scriptDraftStorageKey(`studio.${workspaceId}`, nodeId)
    : null

  const scriptContentRef = useRef(scriptContent)
  scriptContentRef.current = scriptContent
  const nodeIdRef = useRef(nodeId)
  nodeIdRef.current = nodeId

  const scriptAutosave = useScriptAutosave({
    enabled: Boolean(showScriptEditor && canWrite && holdsLock && node && !node.is_locked && nodeId),
    dirty: scriptDirty,
    value: scriptContent,
    storageKey: modalDraftKey,
    entityId: nodeId,
    persist: async (script, entityId) => {
      const id = entityId == null ? null : Number(entityId)
      if (id == null || !Number.isFinite(id) || !nodeRef.current) throw new Error('no node')
      const n = nodeRef.current
      const updated: any = await studioApi.saveDraft(id, {
        workspace_id: workspaceId,
        name: form.getFieldValue('name') || n.name,
        node_type: n.node_type,
        script_content: script,
      })
      if (nodeIdRef.current !== id) return
      const merged = { ...n, ...updated, script_content: script }
      setNode(merged)
      // 同步父页面节点缓存（Studio 打开的 Tab / Workflow 节点列表），避免关弹窗后仍见旧脚本
      onSaved?.(merged)
    },
    onSynced: (script, entityId) => {
      if (entityId == null || nodeIdRef.current !== entityId) return
      if (scriptContentRef.current !== script) return
      setScriptDirty(false)
    },
  })

  const handleClose = async () => {
    if (holdsLockRef.current && scriptDirty) {
      const ok = await scriptAutosave.flush()
      if (!ok) {
        message.warning('草稿同步失败，请检查网络后再关闭；内容已保留在本地')
        return
      }
    }
    if (releaseOnClose && acquiredHereRef.current && nodeId && holdsLockRef.current) {
      try {
        await studioApi.releaseEditLock(nodeId)
      } catch { /* ignore */ }
      acquiredHereRef.current = false
      setHoldsLock(false)
    }
    setScriptDirty(false)
    onClose()
  }

  const handleSteal = () => {
    Modal.confirm({
      title: '抢锁编辑',
      content: `当前编辑锁由「${node?.edit_lock_username || '其他用户'}」持有，确定抢占？`,
      okText: '抢锁',
      zIndex: Z_NODE_CONFIG_CONFIRM,
      onOk: async () => {
        const ok = await tryAcquire(true, false)
        if (ok) message.success('已抢占编辑锁')
      },
    })
  }

  const handleOk = async () => {
    if (!node || !nodeId) return
    if (!canWrite) {
      message.warning('当前角色无节点编辑权限，无法保存')
      return
    }
    if (node.is_locked) {
      message.warning('脚本已锁定（发布治理），无法修改配置；请先在数据开发中解锁')
      return
    }
    let ok = holdsLock
    if (!ok) ok = await tryAcquire(false, true)
    if (!ok) {
      message.warning('请先获取编辑锁后再保存；若由他人占用请使用「抢锁」')
      return
    }
    const values = await form.validateFields()
    const raw = values.params
    if (raw === undefined || raw === null) {
      values.params = null
    } else if (typeof raw === 'string') {
      const s = raw.trim()
      if (s === '') {
        values.params = null
      } else {
        try {
          const parsed = JSON.parse(s)
          if (parsed !== null && (typeof parsed !== 'object' || Array.isArray(parsed))) {
            message.error('自定义变量须为键值对对象 {...}，不能是数组或纯字符串')
            return
          }
          values.params = parsed
        } catch {
          values.params = s
        }
      }
    }
    if (values.timeout_seconds === '' || values.timeout_seconds === undefined) {
      values.timeout_seconds = null
    }
    if (values.retry_times === '' || values.retry_times === undefined) {
      values.retry_times = null
    }
    if (node.node_type === 'SYNC') {
      if (!values.sync_task_id) {
        message.error('请选择要绑定的数据集成任务')
        return
      }
      values.params = { sync_task_id: values.sync_task_id }
      delete values.sync_task_id
    }
    if (node.node_type === 'DEPENDENT') {
      try {
        values.params = dependentFormToParams(values)
      } catch (e: any) {
        message.error(e?.message || '请配置依赖项')
        return
      }
      delete values.relation
      delete values.depend_items
      delete values.depend_workflow_id
      delete values.date_value
    }
    if (node.node_type === 'SQL' || node.node_type === 'PYTHON') {
      values.datasource_id = values.datasource_id ?? null
    }
    if (SCRIPT_NODE_TYPES.has(node.node_type)) {
      values.script_content = values.script_content ?? ''
    } else {
      delete values.script_content
    }
    setSaving(true)
    try {
      const updated: any = await studioApi.updateNode(
        nodeId,
        { ...node, ...values, workspace_id: workspaceId },
        { createHistory: true },
      )
      setNode(updated)
      setScriptDirty(false)
      scriptAutosave.markVersionSaved()
      message.success(SCRIPT_NODE_TYPES.has(node.node_type) ? '已保存并记入版本历史' : '已保存')
      onSaved?.(updated)
      await handleClose()
    } catch (e: any) {
      const d = e?.response?.data?.detail
      const msg = Array.isArray(d)
        ? d.map((x: any) => x.msg || JSON.stringify(x)).join('; ')
        : (typeof d === 'string' ? d : e?.message || '保存失败')
      message.error(msg)
    } finally {
      setSaving(false)
    }
  }

  const lockReadOnly = Boolean(node?.is_locked) || (!holdsLock && Boolean(node?.edit_lock_user_id))
  const formDisabled = !canWrite || Boolean(node?.is_locked)
  const scriptReadOnly = !canWrite || Boolean(node?.is_locked) || !holdsLock

  return (
    <Modal
      title={
        <Space>
          <span>节点配置</span>
          {node && <Tag>{node.node_type}</Tag>}
          {!canWrite && <Tag>只读</Tag>}
          {node?.is_locked && <Tag color="orange">已锁定</Tag>}
          {node?.edit_lock_username && canWrite && (
            <Tag color={holdsLock ? 'green' : 'gold'}>
              编辑锁 {node.edit_lock_username}{holdsLock ? '（我）' : ''}
            </Tag>
          )}
        </Space>
      }
      open={open}
      onOk={() => void handleOk()}
      onCancel={() => void handleClose()}
      confirmLoading={saving}
      okButtonProps={{ disabled: formDisabled }}
      okText={showScriptEditor
        ? `保存版本${scriptAutosave.versionDirty ? ' *' : ''}`
        : '保存'}
      width={920}
      styles={{ body: { maxHeight: 'min(78vh, 820px)', overflowY: 'auto' } }}
      destroyOnClose
      zIndex={Z_NODE_CONFIG}
      footer={(_, { OkBtn, CancelBtn }) => (
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space>
            {canWrite && !node?.is_locked && !holdsLock && node?.edit_lock_username && (
              <Button size="small" danger icon={<LockOutlined />} onClick={handleSteal}>抢锁</Button>
            )}
            <AutosaveStatusHint
              visible={Boolean(showScriptEditor && canWrite && holdsLock && !node?.is_locked)}
              status={scriptAutosave.status}
              hint={scriptAutosave.hint}
            />
          </Space>
          <Space>
            <CancelBtn />
            {canWrite && <OkBtn />}
          </Space>
        </Space>
      )}
    >
      <Spin spinning={loading}>
        <Form form={form} layout="vertical" style={{ marginTop: 8 }} disabled={formDisabled}>
          <Form.Item name="name" label="节点名称" rules={[{ required: true }]} style={{ marginBottom: 12 }}>
            <Input />
          </Form.Item>
          <Tabs
            defaultActiveKey="primary"
            items={[
              {
                key: 'primary',
                label: primaryTabLabel,
                children: (
                  <div>
                    {(node?.node_type === 'SQL' || node?.node_type === 'PYTHON') && (
                      <Form.Item
                        name="datasource_id"
                        label="数据源（可选）"
                        extra={
                          node?.node_type === 'PYTHON'
                            ? (currentWorkspace?.default_datasource_id
                              ? 'PYTHON 用 gido_job.execute 读库；不选则继承空间默认'
                              : '请先在「空间设置」配置默认数据源或在此指定')
                            : (currentWorkspace?.default_datasource_id
                              ? '不选则继承空间默认；选定后该节点固定此数据源'
                              : '请先在「空间设置」配置默认数据源')
                        }
                      >
                        <Select
                          allowClear
                          placeholder={
                            dsResolve?.source === 'workspace' && dsResolve.effective
                              ? `继承空间默认：${dsResolve.effective.name}`
                              : '继承空间默认'
                          }
                          options={datasources.map((d: any) => ({ label: `${d.name} (${d.ds_type})`, value: d.id }))}
                        />
                      </Form.Item>
                    )}
                    {showScriptEditor && (
                      <Form.Item
                        name="script_content"
                        label="脚本内容"
                        extra={node?.node_type === 'PYTHON'
                          ? '与数据开发同一脚本；可用 gido_job.execute / writelog；后台自动落草稿，点「保存版本」记入历史'
                          : '与数据开发同一脚本；后台自动落草稿，点「保存版本」记入历史'}
                      >
                        <div style={{ border: '1px solid #d9d9d9', borderRadius: 6, overflow: 'hidden', position: 'relative' }}>
                          <MonacoFindBar
                            getEditor={() => editorRef.current}
                            apiRef={findApiRef}
                            readOnly={scriptReadOnly}
                            theme={editorAppearance.theme}
                          />
                          <Editor
                            height={360}
                            language={SCRIPT_LANG[node!.node_type] || 'plaintext'}
                            theme={editorAppearance.theme}
                            beforeMount={registerDwMonacoThemes}
                            onMount={(ed, monaco) => {
                              editorRef.current = ed
                              bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
                            }}
                            value={scriptContent}
                            onChange={(v) => {
                              if (scriptReadOnly) return
                              form.setFieldsValue({ script_content: v ?? '' })
                              setScriptDirty(true)
                            }}
                            options={{
                              ...monacoEditorOptionsFromAppearance(editorAppearance),
                              readOnly: scriptReadOnly,
                              minimap: { enabled: false },
                              scrollBeyondLastLine: false,
                            }}
                          />
                        </div>
                      </Form.Item>
                    )}
                    {node?.node_type === 'SYNC' && (
                      <>
                        <Form.Item
                          name="sync_task_id"
                          label="绑定的数据集成任务"
                          rules={[{ required: true, message: '请选择同步任务' }]}
                          extra="SYNC 节点执行所选集成任务；任务定义在「数据集成」中维护，此处完成绑定即可"
                        >
                          <Select
                            showSearch
                            optionFilterProp="label"
                            placeholder="选择同步任务"
                            options={integrationTasks.map((t: any) => ({
                              label: `${t.name} (#${t.id}, ${t.sync_mode})`,
                              value: t.id,
                            }))}
                          />
                        </Form.Item>
                        {selectedSyncTask ? (
                          <Card size="small" title="任务详情" style={{ marginBottom: 12 }}>
                            <Descriptions size="small" column={1}>
                              <Descriptions.Item label="名称">{selectedSyncTask.name}</Descriptions.Item>
                              <Descriptions.Item label="模式">{selectedSyncTask.sync_mode}</Descriptions.Item>
                              <Descriptions.Item label="ID">#{selectedSyncTask.id}</Descriptions.Item>
                              <Descriptions.Item label="源表">
                                #{selectedSyncTask.src_datasource_id} / {selectedSyncTask.src_table || '—'}
                              </Descriptions.Item>
                              <Descriptions.Item label="目标表">
                                #{selectedSyncTask.dst_datasource_id} / {selectedSyncTask.dst_table || '—'}
                              </Descriptions.Item>
                            </Descriptions>
                          </Card>
                        ) : (
                          <div style={{ color: '#999', fontSize: 12, marginBottom: 12 }}>
                            选择任务后可在此查看摘要；完整映射仍在「数据集成」编辑。
                          </div>
                        )}
                        <div style={{ color: '#666', fontSize: 12, marginBottom: 4 }}>节点参数预览</div>
                        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, fontSize: 12, margin: 0 }}>
                          {JSON.stringify({ sync_task_id: syncTaskId ?? null }, null, 2)}
                        </pre>
                      </>
                    )}
                    {node?.node_type === 'DEPENDENT' && (
                      <>
                        <Form.Item
                          name="relation"
                          label="多依赖关系"
                          initialValue="AND"
                          extra="同一节点内多条依赖按 AND/OR 组合；发布到 Dolphin 后按调度窗口内最近成功实例判断（非「窗口内全部成功」）"
                        >
                          <Radio.Group
                            options={[
                              { label: '全部满足 (AND)', value: 'AND' },
                              { label: '任一满足 (OR)', value: 'OR' },
                            ]}
                          />
                        </Form.Item>
                        <Form.List name="depend_items" initialValue={[{ depend_workflow_id: null, date_value: 'today' }]}>
                          {(fields, { add, remove }) => (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
                              {fields.map((field, idx) => (
                                <Card
                                  key={field.key}
                                  size="small"
                                  title={`依赖项 ${idx + 1}`}
                                  extra={
                                    fields.length > 1 ? (
                                      <Button
                                        type="text"
                                        danger
                                        size="small"
                                        icon={<DeleteOutlined />}
                                        onClick={() => remove(field.name)}
                                      />
                                    ) : null
                                  }
                                >
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'depend_workflow_id']}
                                    label="依赖的工作流"
                                    rules={[{ required: true, message: '请选择工作流' }]}
                                    style={{ marginBottom: 8 }}
                                  >
                                    <Select
                                      showSearch
                                      optionFilterProp="label"
                                      placeholder="选择同空间工作流"
                                      options={workflows.map((w: any) => ({
                                        label: `${w.name} (#${w.id})${w.scheduler_definition_id ? '' : ' · 未发布'}`,
                                        value: w.id,
                                      }))}
                                    />
                                  </Form.Item>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'date_value']}
                                    label="依赖时段"
                                    rules={[{ required: true }]}
                                    style={{ marginBottom: 0 }}
                                    initialValue="today"
                                  >
                                    <Select options={DEPENDENT_DATE_OPTIONS.map(o => ({ label: o.label, value: o.value }))} />
                                  </Form.Item>
                                </Card>
                              ))}
                              <Button type="dashed" onClick={() => add({ depend_workflow_id: null, date_value: 'today' })} block icon={<PlusOutlined />}>
                                添加依赖项
                              </Button>
                            </div>
                          )}
                        </Form.List>
                        <div style={{ color: '#999', fontSize: 12, marginBottom: 12 }}>
                          若上游是小时调度、下游要等齐当天，建议依赖日终收口工作流，或配置 last24Hours 等时段（仍按最近成功实例，非全部实例）。
                        </div>
                        <div style={{ color: '#666', fontSize: 12, marginBottom: 4 }}>节点参数预览</div>
                        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, fontSize: 12, margin: 0 }}>
                          {JSON.stringify(dependentPreview, null, 2)}
                        </pre>
                      </>
                    )}
                  </div>
                ),
              },
              {
                key: 'runtime',
                label: '运行参数',
                children: (
                  <div>
                    <Form.Item name="timeout_seconds" label="超时时间（秒）">
                      <Input type="number" />
                    </Form.Item>
                    <Form.Item name="retry_times" label="失败重试次数">
                      <Input type="number" />
                    </Form.Item>
                    {SCRIPT_NODE_TYPES.has(node?.node_type) && (
                      <Form.Item
                        name="params"
                        label="自定义变量（对象）"
                        tooltip={'标准 JSON 用双引号；含时间宏的键会同步到 Dolphin 全局参数'}
                      >
                        <Input.TextArea rows={4} placeholder={'{"xx": "yy"}'} disabled={formDisabled || (lockReadOnly && !holdsLock)} />
                      </Form.Item>
                    )}
                    {(node?.node_type === 'SYNC' || node?.node_type === 'DEPENDENT') && (
                      <div style={{ color: '#999', fontSize: 12 }}>
                        {node.node_type === 'SYNC'
                          ? 'SYNC 的业务参数即绑定的同步任务（见「同步任务」页），此处只调超时与重试。'
                          : 'DEPENDENT 的业务参数即依赖项配置（见「依赖配置」页），此处只调超时与重试。'}
                      </div>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <div style={{ color: '#999', fontSize: 12, marginTop: 8 }}>
            与「数据开发」共用同一节点与编辑锁；各类型节点均可在此完成主内容与运行参数编辑。
          </div>
        </Form>
      </Spin>
    </Modal>
  )
}
