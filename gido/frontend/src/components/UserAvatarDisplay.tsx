/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Avatar } from 'antd'
import type { AvatarProps } from 'antd'
import { presetAvatarDataUri } from '../utils/avatarPresetArt'
import {
  avatarUploadUrl,
  isAvatarPresetId,
  parseAvatarRef,
  userDisplayInitial,
  userInitialBackground,
} from '../utils/userAvatar'

type UserLike = {
  username?: string
  full_name?: string | null
  avatar?: string | null
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

  if (ref.kind === 'preset' && ref.id && isAvatarPresetId(ref.id)) {
    const src = presetAvatarDataUri(ref.id)
    if (src) {
      return (
        <Avatar
          {...rest}
          className={className}
          src={src}
          alt={`头像 ${ref.id}`}
        />
      )
    }
  }

  return (
    <Avatar
      {...rest}
      className={className}
      style={{
        backgroundColor: userInitialBackground(user),
        color: '#fff',
        fontWeight: 600,
        ...(typeof rest.style === 'object' ? rest.style : {}),
      }}
    >
      {initial}
    </Avatar>
  )
}
