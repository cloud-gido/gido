/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Form, Input, Button, Card, message, Modal } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import ProductBrandBlock from '../components/ProductBrandBlock'
import GidoLoginBrand from '../components/GidoLoginBrand'
import { Link, useNavigate } from 'react-router-dom'
import { authApi } from '../api'
import { useAppStore } from '../store'
import { R } from '../routes'
import { BRAND, OPEN_SOURCE } from '../branding'
import { can, P } from '../perm'

function WorkbenchCard({ variant, onClick }: { variant: 'batch' | 'stream' | 'service'; onClick: () => void }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => { if (e.key === 'Enter') onClick() }}
      className={`dw-login-workbench-card dw-login-workbench-card--${variant}`}
    >
      <ProductBrandBlock variant={variant} markSize={28} showTagline className="dw-workbench-card-brand" />
    </div>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const { setUser } = useAppStore()

  const onFinish = async (values: any) => {
    try {
      const res: any = await authApi.login(values.username, values.password)
      localStorage.setItem('token', res.access_token)
      setUser(res.user)
      message.success('登录成功')

      const u = res.user
      const cards = [
        <WorkbenchCard key="batch" variant="batch" onClick={() => { Modal.destroyAll(); navigate(R.batch.studio) }} />,
      ]
      if (can(u, P.GIDO_STREAM_READ)) {
        cards.push(
          <WorkbenchCard key="stream" variant="stream" onClick={() => { Modal.destroyAll(); navigate(R.stream.studio) }} />,
        )
      }
      if (can(u, P.GIDO_SERVICE_READ)) {
        cards.push(
          <WorkbenchCard key="service" variant="service" onClick={() => { Modal.destroyAll(); navigate(R.service.overview) }} />,
        )
      }

      Modal.confirm({
        title: `进入 ${BRAND.suiteFull}`,
        icon: null,
        width: Math.min(760, 120 + cards.length * 200),
        className: 'dw-login-workbench-modal',
        content: (
          <div className="dw-login-workbench-grid">
            {cards}
          </div>
        ),
        footer: null,
        closable: true,
        onCancel: () => navigate(R.batch.studio),
      })
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      const msg =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((x: any) => x.msg ?? JSON.stringify(x)).join('; ')
            : e?.message === 'Network Error'
              ? '无法连接后端，请检查服务是否启动或代理配置'
              : '用户名或密码错误'
      message.error({ content: msg, key: 'login' })
    }
  }

  return (
    <div className="dw-login-bg dw-login-bg--gido">
      <Card className="dw-login-card dw-login-card--gido">
        <GidoLoginBrand />
        <Form onFinish={onFinish} layout="vertical" requiredMark={false} initialValues={{ username: 'admin', password: 'admin123' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" size="large" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
            <Button type="primary" htmlType="submit" block size="large" className="dw-login-submit">
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
      <footer className="dw-login-footer">
        <a href={OPEN_SOURCE.licenseUrl} target="_blank" rel="noopener noreferrer">
          {OPEN_SOURCE.license}
        </a>
        <span className="dw-login-footer__sep">·</span>
        <span className="dw-login-footer__sep">·</span>
        <Link to={R.about}>关于</Link>
      </footer>
    </div>
  )
}
