/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Select, Space, Switch, Tabs, message } from 'antd'
import { ApiOutlined, DatabaseOutlined, ExperimentOutlined } from '@ant-design/icons'
import { useAppStore } from '../store'
import { datasourceApi, workspaceApi } from '../api'

export default function WorkspaceSettingsPage() {
  const { currentWorkspace, setCurrentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const isAdmin = currentWorkspace?.my_role === 'admin'

  const [datasources, setDatasources] = useState<any[]>([])
  const [defaultsForm] = Form.useForm()
  const [dolphinForm] = Form.useForm()
  const [flinkForm] = Form.useForm()
  const [dolphinMeta, setDolphinMeta] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const loadAll = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const [ds, def, dol]: any[] = await Promise.all([
        datasourceApi.list(wsId),
        workspaceApi.getDefaults(wsId),
        workspaceApi.getDolphin(wsId),
      ])
      setDatasources(Array.isArray(ds) ? ds : [])
      defaultsForm.setFieldsValue({
        default_datasource_id: def.default_datasource_id,
        warehouse_datasource_id: def.warehouse_datasource_id ?? def.effective_warehouse_datasource_id,
      })
      setDolphinMeta(dol)
      dolphinForm.setFieldsValue({
        ds_enabled: dol.override_enabled ?? dol.effective_enabled,
        ds_url: dol.override_url ?? '',
        ds_ui_url: dol.override_ui_url ?? '',
        ds_project_name: dol.override_project_name ?? dol.effective_project_name,
        ds_token: '',
      })
      try {
        const fl: any = await workspaceApi.getFlink(wsId)
        flinkForm.setFieldsValue(fl.override || {})
      } catch {
        /* flink optional */
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载空间设置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadAll() }, [wsId])

  const dsOptions = datasources.map((d: any) => ({
    label: `${d.name} (${d.ds_type})`,
    value: d.id,
  }))

  const saveDefaults = async () => {
    if (!wsId) return
    const v = await defaultsForm.validateFields()
    await workspaceApi.putDefaults(wsId, {
      default_datasource_id: v.default_datasource_id ?? null,
      warehouse_datasource_id: v.warehouse_datasource_id ?? null,
      clear_default_datasource: v.default_datasource_id == null,
      clear_warehouse_datasource: v.warehouse_datasource_id == null,
    })
    const refreshed: any = await workspaceApi.get(wsId)
    setCurrentWorkspace({ ...currentWorkspace, ...refreshed })
    message.success('默认数据源已保存')
  }

  const saveDolphin = async () => {
    if (!wsId) return
    const v = await dolphinForm.validateFields()
    const body: Record<string, unknown> = {
      ds_enabled: v.ds_enabled,
      ds_url: v.ds_url || null,
      ds_ui_url: v.ds_ui_url ?? null,
      ds_project_name: v.ds_project_name || null,
    }
    if (v.ds_token) body.ds_token = v.ds_token
    await workspaceApi.putDolphin(wsId, body)
    message.success('本空间 Dolphin 配置已保存')
    await loadAll()
  }

  const testDolphin = async () => {
    if (!wsId) return
    const r: any = await workspaceApi.testDolphin(wsId)
    if (r?.ok) message.success(r.message || '连接成功')
    else message.error(r?.message || '连接失败')
  }

  const saveFlink = async () => {
    if (!wsId) return
    const v = await flinkForm.validateFields()
    await workspaceApi.putFlink(wsId, v)
    message.success('本空间 Flink 配置已保存')
  }

  if (!wsId) {
    return <Alert type="info" message="请先选择工作空间" showIcon />
  }

  if (!isAdmin) {
    return <Alert type="warning" message="仅工作空间管理员可修改空间设置" showIcon />
  }

  return (
    <div style={{ maxWidth: 880, margin: '0 auto' }}>
      <h2 style={{ marginTop: 0 }}>空间设置 · {currentWorkspace?.name}</h2>
      <p style={{ color: '#666', fontSize: 13, marginBottom: 16 }}>
        按工作空间区分测试与生产。数据源规则：<strong>已在节点/查询「配置」里选过数据源的脚本保持不动</strong>；
        <strong>未单独配置的脚本</strong>在运行时使用下方「默认数据源」。修改空间默认后，仅影响未单独绑定的脚本。
      </p>

      <Tabs
        items={[
          {
            key: 'ds',
            label: (
              <span>
                <DatabaseOutlined /> 默认数据源
              </span>
            ),
            children: (
              <Card loading={loading}>
                <Form form={defaultsForm} layout="vertical">
                  <Form.Item
                    name="default_datasource_id"
                    label="默认数据源（数据开发 / 探查 / SQL 节点）"
                    extra="未在脚本/节点配置里单独指定数据源时，运行与探查将使用此项。已配置过的旧脚本不受影响。"
                  >
                    <Select allowClear options={dsOptions} placeholder="选择本空间数据源" />
                  </Form.Item>
                  <Form.Item
                    name="warehouse_datasource_id"
                    label="数仓数据源（数据集成默认目标）"
                    extra="集成任务新建时默认填入目标库；不填则与默认数据源相同。"
                  >
                    <Select allowClear options={dsOptions} placeholder="通常选 Doris / PostgreSQL 数仓" />
                  </Form.Item>
                  <Button type="primary" onClick={saveDefaults}>
                    保存数据源设置
                  </Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'dolphin',
            label: (
              <span>
                <ApiOutlined /> Dolphin
              </span>
            ),
            children: (
              <Card loading={loading}>
                {dolphinMeta && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message={`当前生效：${dolphinMeta.effective_enabled ? '已启用' : '未启用'} · ${dolphinMeta.effective_url || '—'}（来源：${dolphinMeta.effective_url_source}）`}
                  />
                )}
                <Form form={dolphinForm} layout="vertical">
                  <Form.Item name="ds_enabled" label="启用 Dolphin" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="ds_url" label="API 地址">
                    <Input placeholder="http://host:12345/dolphinscheduler" />
                  </Form.Item>
                  <Form.Item name="ds_ui_url" label="UI 地址（浏览器打开）">
                    <Input placeholder="可选" />
                  </Form.Item>
                  <Form.Item name="ds_project_name" label="项目名称">
                    <Input />
                  </Form.Item>
                  <Form.Item name="ds_token" label="Token（留空不修改）">
                    <Input.Password placeholder={dolphinMeta?.token_masked ? `已配置 ${dolphinMeta.token_masked}` : '输入新 Token'} />
                  </Form.Item>
                  <Space>
                    <Button type="primary" onClick={saveDolphin}>
                      保存
                    </Button>
                    <Button icon={<ExperimentOutlined />} onClick={testDolphin}>
                      测试连接
                    </Button>
                  </Space>
                </Form>
              </Card>
            ),
          },
          {
            key: 'flink',
            label: <span><ApiOutlined /> Flink</span>,
            children: (
              <Card loading={loading}>
                <Form form={flinkForm} layout="vertical">
                  <Form.Item name="flink_url" label="Flink REST">
                    <Input placeholder="留空沿用全局/环境变量" />
                  </Form.Item>
                  <Form.Item name="flink_sql_gateway_url" label="SQL Gateway">
                    <Input />
                  </Form.Item>
                  <Form.Item name="flink_ui_url" label="Flink UI">
                    <Input />
                  </Form.Item>
                  <Button type="primary" onClick={saveFlink}>
                    保存 Flink
                  </Button>
                </Form>
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}
