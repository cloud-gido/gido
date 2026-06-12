/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Avatar } from 'antd'
import type { AvatarProps } from 'antd'
import {
  AVATAR_PRESET_COLORS,
  avatarUploadUrl,
  isAvatarPresetId,
  parseAvatarRef,
  userDisplayInitial,
} from '../utils/userAvatar'

type UserLike = {
  username?: string
  full_name?: string | null
  avatar?: string | null
}

function PresetFace({ id }: { id: string }) {
  const color = isAvatarPresetId(id) ? AVATAR_PRESET_COLORS[id] : '#64748b'
  const eyeY = 18
  const mouth =
    id === '1' || id === '5'
      ? <path d="M14 30 Q20 36 26 30" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" />
      : id === '2' || id === '6'
        ? <path d="M14 32 Q20 28 26 32" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" />
        : <line x1="14" y1="32" x2="26" y2="32" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
  return (
    <svg viewBox="0 0 40 40" width="100%" height="100%" aria-hidden>
      <rect width="40" height="40" fill={color} />
      <circle cx="14" cy={eyeY} r="2.5" fill="#fff" />
      <circle cx="26" cy={eyeY} r="2.5" fill="#fff" />
      {mouth}
    </svg>
  )
}

export default function UserAvatarDisplay({
  user,
  className,
  ...rest
}: { user?: UserLike | null } & AvatarProps) {
  const ref = parseAvatarRef(user?.avatar)
  const initial = userDisplayInitial(user)

  if (ref.kind === 'upload' && ref.id) {
    return (
      <Avatar
        {...rest}
        className={className}
        src={avatarUploadUrl(ref.id)}
        alt={user?.username || '头像'}
      />
    )
  }

  if (ref.kind === 'preset' && ref.id) {
    return (
      <Avatar {...rest} className={className} alt={`头像 ${ref.id}`}>
        <PresetFace id={ref.id} />
      </Avatar>
    )
  }

  return (
    <Avatar {...rest} className={className}>
      {initial}
    </Avatar>
  )
}
