/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState } from 'react'
import { Dropdown, Space, Modal, Form, Input, Button, Typography, Tooltip, message } from 'antd'
import type { MenuProps } from 'antd'
import {
  LogoutOutlined,
  KeyOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  BgColorsOutlined,
  CheckOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import { useNavigate, Link } from 'react-router-dom'
import { authApi } from '../api'
import type { AppearancePreset } from '../store'
import { useAppStore } from '../store'
import { UI_THEME_IDS, type UiThemeId } from '../appearance/themeIds'

const FIXED_THEME_LABELS: Record<UiThemeId, string> = {
  lightClassic: '清爽经典',
  warmPaper: '护眼暖纸',
  coolMist: '冷雾蓝灰',
  eveningMist: '暮霭静蓝（浅）',
  mintWater: '薄荷清水',
}
import { R } from '../routes'
import { can, isPlatformAdmin, P } from '../perm'
import { BRAND } from '../branding'
import UserAvatarDisplay from './UserAvatarDisplay'
import AvatarPickerModal from './AvatarPickerModal'

const { Text } = Typography

export default function UserAccountMenu() {
  const navigate = useNavigate()
  const { user, logout, currentWorkspace, appearancePreset, setAppearancePreset } = useAppStore()

  const pickIcon = (active: boolean) =>
    (active ? <CheckOutlined /> : <span aria-hidden style={{ display: 'inline-block', width: 14 }} />)

  const onPickPreset = (p: AppearancePreset) => {
    setAppearancePreset(p)
  }
  const [avatarOpen, setAvatarOpen] = useState(false)
  const [pwdOpen, setPwdOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  const handleLogout = () => {
    logout()
    navigate(R.login)
    message.success('已退出登录')
  }

  const submitPassword = async () => {
    const v = await form.validateFields()
    setSubmitting(true)
    try {
      await authApi.changePassword(v.current_password, v.new_password)
      message.success('密码已更新，请重新登录')
      setPwdOpen(false)
      form.resetFields()
      logout()
      navigate(R.login)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '修改失败')
    } finally {
      setSubmitting(false)
    }
  }

  const items: MenuProps['items'] = [
    {
      key: 'avatar',
      icon: <UserAvatarDisplay user={user} size={18} />,
      label: '更换头像',
      onClick: () => setAvatarOpen(true),
    },
    {
      key: 'appearance',
      icon: <BgColorsOutlined />,
      label: '界面与背景',
      children: [
        {
          key: 'preset-auto',
          icon: pickIcon(appearancePreset === 'auto'),
          label: '跟随日出日落（默认 · 日间经典 / 晚间暖灰）',
          onClick: () => onPickPreset('auto'),
        },
        {
          key: 'fixed-themes-divider',
          type: 'divider',
        },
        ...UI_THEME_IDS.map((id): NonNullable<MenuProps['items']>[number] => ({
          key: `preset-fixed-${id}`,
          icon: pickIcon(appearancePreset === id),
          label: FIXED_THEME_LABELS[id],
          onClick: () => onPickPreset(id),
        })),
      ],
    },
    { type: 'divider' },
    {
      key: 'pwd',
      icon: <KeyOutlined />,
      label: '修改密码',
      onClick: () => {
        form.resetFields()
        setPwdOpen(true)
      },
    },
    ...(isPlatformAdmin(user)
      || can(user, P.SYSTEM_ROLE_READ)
      || can(user, P.SYSTEM_INTEGRATION_READ)
      ? [{
          key: 'admin',
          icon: <SettingOutlined />,
          label: <Link to={R.batch.admin}>系统管理</Link>,
        } satisfies NonNullable<MenuProps['items']>[number]]
      : []),
    {
      key: 'about',
      icon: <InfoCircleOutlined />,
      label: <Link to={R.about}>关于 {BRAND.suiteEn}</Link>,
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
      onClick: handleLogout,
    },
  ]

  return (
    <>
      <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
        <Space style={{ cursor: 'pointer' }} size={10}>
          <Tooltip
            title={
              <span>
                {user?.email && <>邮箱：{user.email}<br /></>}
                {user?.full_name && <>姓名：{user.full_name}<br /></>}
                {user?.role_name && <>角色：{user.role_name}</>}
                {!user?.email && !user?.full_name && !user?.role_name && '账号菜单'}
              </span>
            }
          >
            <span
              className="dw-user-avatar-trigger"
              role="button"
              tabIndex={0}
              onClick={(e) => { e.stopPropagation(); setAvatarOpen(true) }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  e.stopPropagation()
                  setAvatarOpen(true)
                }
              }}
            >
              <UserAvatarDisplay user={user} size="small" className="dw-user-avatar" />
            </span>
          </Tooltip>
          <Space direction="vertical" size={0} style={{ lineHeight: 1.15, alignItems: 'flex-start' }}>
            <Text style={{ fontSize: 14 }}>{user?.username ?? '…'}</Text>
            {(user?.role_name || isPlatformAdmin(user)) && (
              <Text type="secondary" style={{ fontSize: 11, maxWidth: 140 }} ellipsis>
                {isPlatformAdmin(user) ? '管理员' : user?.role_name}
              </Text>
            )}
          </Space>
        </Space>
      </Dropdown>

      <AvatarPickerModal open={avatarOpen} onClose={() => setAvatarOpen(false)} />

      <Modal
        title={<><SafetyCertificateOutlined style={{ marginRight: 8 }} />修改登录密码</>}
        open={pwdOpen}
        onCancel={() => { setPwdOpen(false); form.resetFields() }}
        footer={[
          <Button key="cancel" onClick={() => setPwdOpen(false)}>取消</Button>,
          <Button key="ok" type="primary" loading={submitting} onClick={submitPassword}>
            保存
          </Button>,
        ]}
        destroyOnClose
        width={420}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 14, fontSize: 12 }}>
          新密码至少 8 位。修改成功后需要使用新密码重新登录。
          {!can(user, P.SYSTEM_ROLE_READ) && !can(user, P.SYSTEM_INTEGRATION_READ) && (
            <>
              {' '}若遗忘密码，请联系具备「系统管理」权限的同事重置。
            </>
          )}
        </Text>
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="current_password"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '至少 8 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再次输入' },
              ({ getFieldValue }) => ({
                validator(_, val) {
                  if (!val || getFieldValue('new_password') === val) return Promise.resolve()
                  return Promise.reject(new Error('两次输入不一致'))
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
