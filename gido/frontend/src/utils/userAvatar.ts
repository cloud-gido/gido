/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { allBuiltinPresetIds, AVATAR_LEGACY_PRESET_IDS } from './avatarCatalog'

const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
const apiBase = apiOrigin ? `${apiOrigin}/api` : '/api'

export const AVATAR_PRESET_IDS = allBuiltinPresetIds()
export type AvatarPresetId = string

/** 无自定义头像时，按用户名生成稳定色（类似 Google 账号首字母圆标） */
const INITIAL_COLORS = ['#1a73e8', '#e37400', '#0d652d', '#a142f4', '#c5221f', '#007b83', '#5f6368', '#9334e6']

const STYLE_PRESET_RE = /^(emoji|lorelei|notion|smile|avataaars|personas|bottts|adventurer|pixel|micah)-[1-6]$/

export function userInitialBackground(user?: { username?: string; full_name?: string | null } | null): string {
  const name = (user?.full_name || user?.username || '').trim()
  if (!name) return INITIAL_COLORS[0]
  let hash = 0
  for (let i = 0; i < name.length; i += 1) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  return INITIAL_COLORS[Math.abs(hash) % INITIAL_COLORS.length]
}

export function userDisplayInitial(user?: { username?: string; full_name?: string | null } | null): string {
  const name = (user?.full_name || user?.username || '?').trim()
  if (!name) return '?'
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

export function parseAvatarRef(avatar?: string | null): { kind: 'preset' | 'upload' | 'none'; id?: string } {
  if (!avatar) return { kind: 'none' }
  if (avatar.startsWith('preset:')) return { kind: 'preset', id: avatar.slice('preset:'.length) }
  if (avatar.startsWith('upload:')) return { kind: 'upload', id: avatar.slice('upload:'.length) }
  return { kind: 'none' }
}

export function avatarUploadUrl(storedName: string): string {
  return `${apiBase}/auth/avatars/${encodeURIComponent(storedName)}`
}

export function isAvatarPresetId(id: string): boolean {
  if ((AVATAR_LEGACY_PRESET_IDS as readonly string[]).includes(id)) return true
  return STYLE_PRESET_RE.test(id)
}
