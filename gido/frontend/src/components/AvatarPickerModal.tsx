/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useRef, useState } from 'react'
import { Modal, Button, Upload, Typography, message } from 'antd'
import { UploadOutlined, UserOutlined } from '@ant-design/icons'
import { authApi } from '../api'
import { useAppStore } from '../store'
import UserAvatarDisplay from './UserAvatarDisplay'
import { AVATAR_PRESET_IDS, parseAvatarRef, type AvatarPresetId } from '../utils/userAvatar'

const { Text } = Typography

const MAX_BYTES = 2 * 1024 * 1024

type Props = {
  open: boolean
  onClose: () => void
}

export default function AvatarPickerModal({ open, onClose }: Props) {
  const { user, setUser } = useAppStore()
  const [saving, setSaving] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const current = parseAvatarRef(user?.avatar)

  const applyUser = (next: typeof user) => {
    setUser(next)
  }

  const pickPreset = async (id: AvatarPresetId) => {
    setSaving(true)
    try {
      const next = await authApi.updateAvatar(`preset:${id}`)
      applyUser(next)
      message.success('头像已更新')
      onClose()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const resetDefault = async () => {
    setSaving(true)
    try {
      const next = await authApi.updateAvatar(null)
      applyUser(next)
      message.success('已恢复默认头像')
      onClose()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const onFile = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      message.error('请选择图片文件')
      return false
    }
    if (file.size > MAX_BYTES) {
      message.error('图片不能超过 2MB')
      return false
    }
    setSaving(true)
    try {
      const next = await authApi.uploadAvatar(file)
      applyUser(next)
      message.success('头像已上传')
      onClose()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '上传失败')
    } finally {
      setSaving(false)
    }
    return false
  }

  return (
    <Modal
      title="更换头像"
      open={open}
      onCancel={onClose}
      footer={null}
      width={420}
      destroyOnClose
    >
      <div className="dw-avatar-picker-preview">
        <UserAvatarDisplay user={user} size={72} />
        <Text type="secondary" style={{ fontSize: 12 }}>
          点击选择内置头像，或上传自定义图片（PNG / JPEG / WebP，≤2MB）
        </Text>
      </div>

      <div className="dw-avatar-picker-grid">
        {AVATAR_PRESET_IDS.map((id) => (
          <button
            key={id}
            type="button"
            className={
              'dw-avatar-picker-item'
              + (current.kind === 'preset' && current.id === id ? ' dw-avatar-picker-item--active' : '')
            }
            disabled={saving}
            onClick={() => pickPreset(id)}
            aria-label={`内置头像 ${id}`}
          >
            <UserAvatarDisplay user={{ avatar: `preset:${id}` }} size={44} />
          </button>
        ))}
      </div>

      <div className="dw-avatar-picker-actions">
        <Upload
          accept="image/png,image/jpeg,image/webp"
          showUploadList={false}
          beforeUpload={onFile}
          disabled={saving}
        >
          <Button icon={<UploadOutlined />} loading={saving}>
            上传自定义头像
          </Button>
        </Upload>
        <Button icon={<UserOutlined />} disabled={saving} onClick={resetDefault}>
          恢复默认（姓名首字母）
        </Button>
      </div>
      <input ref={fileRef} type="file" hidden aria-hidden tabIndex={-1} />
    </Modal>
  )
}
