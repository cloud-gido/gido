/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
const apiBase = apiOrigin ? `${apiOrigin}/api` : '/api'

export const AVATAR_PRESET_IDS = ['1', '2', '3', '4', '5', '6', '7', '8'] as const
export type AvatarPresetId = (typeof AVATAR_PRESET_IDS)[number]

/** preset 背景色（与 AvatarPresetFace 一致） */
export const AVATAR_PRESET_COLORS: Record<AvatarPresetId, string> = {
  '1': '#3b82f6',
  '2': '#10b981',
  '3': '#f59e0b',
  '4': '#8b5cf6',
  '5': '#ef4444',
  '6': '#06b6d4',
  '7': '#ec4899',
  '8': '#64748b',
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

export function isAvatarPresetId(id: string): id is AvatarPresetId {
  return (AVATAR_PRESET_IDS as readonly string[]).includes(id)
}
