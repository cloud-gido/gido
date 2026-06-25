/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

export type AvatarStyleKey =
  | 'emoji'
  | 'lorelei'
  | 'notion'
  | 'smile'
  | 'avataaars'
  | 'personas'
  | 'bottts'
  | 'adventurer'
  | 'pixel'
  | 'micah'

export type AvatarStyleGroup = {
  key: AvatarStyleKey
  label: string
  description: string
  /** 每风格 6 个固定 seed，保证头像稳定可复现 */
  seeds: readonly [string, string, string, string, string, string]
  backgrounds: readonly [string, string, string, string, string, string]
}

const BG_WARM = ['fef3c7', 'ffedd5', 'ffe4e6', 'fce7f3', 'fed7aa', 'fde68a'] as const
const BG_COOL = ['dbeafe', 'cffafe', 'd1fae5', 'e0e7ff', 'ccfbf1', 'e2e8f0'] as const
const BG_VIVID = ['ede9fe', 'fae8ff', 'fecdd3', 'bbf7d0', 'bfdbfe', 'ddd6fe'] as const

/** 内置头像风格分组（不含 legacy 1-8） */
export const AVATAR_STYLE_GROUPS: AvatarStyleGroup[] = [
  {
    key: 'emoji',
    label: '表情',
    description: '类似 Google 账号的趣味表情',
    seeds: ['gido-emoji-a', 'gido-emoji-b', 'gido-emoji-c', 'gido-emoji-d', 'gido-emoji-e', 'gido-emoji-f'],
    backgrounds: BG_WARM,
  },
  {
    key: 'lorelei',
    label: '插画人物',
    description: '简约手绘人像',
    seeds: ['gido-lorelei-a', 'gido-lorelei-b', 'gido-lorelei-c', 'gido-lorelei-d', 'gido-lorelei-e', 'gido-lorelei-f'],
    backgrounds: BG_COOL,
  },
  {
    key: 'notion',
    label: 'Notion 风',
    description: '干净利落的线条人物',
    seeds: ['gido-notion-a', 'gido-notion-b', 'gido-notion-c', 'gido-notion-d', 'gido-notion-e', 'gido-notion-f'],
    backgrounds: BG_COOL,
  },
  {
    key: 'smile',
    label: '笑脸',
    description: '圆润开朗的卡通脸',
    seeds: ['gido-smile-a', 'gido-smile-b', 'gido-smile-c', 'gido-smile-d', 'gido-smile-e', 'gido-smile-f'],
    backgrounds: BG_WARM,
  },
  {
    key: 'avataaars',
    label: '卡通头像',
    description: '接近 Apple Memoji 的卡通人物',
    seeds: ['gido-ava-a', 'gido-ava-b', 'gido-ava-c', 'gido-ava-d', 'gido-ava-e', 'gido-ava-f'],
    backgrounds: BG_VIVID,
  },
  {
    key: 'personas',
    label: '人物剪影',
    description: '偏商务的扁平人物',
    seeds: ['gido-persona-a', 'gido-persona-b', 'gido-persona-c', 'gido-persona-d', 'gido-persona-e', 'gido-persona-f'],
    backgrounds: BG_COOL,
  },
  {
    key: 'bottts',
    label: '机器人',
    description: 'Chrome 扩展风格的机器人',
    seeds: ['gido-bot-a', 'gido-bot-b', 'gido-bot-c', 'gido-bot-d', 'gido-bot-e', 'gido-bot-f'],
    backgrounds: BG_VIVID,
  },
  {
    key: 'adventurer',
    label: '冒险者',
    description: '活泼冒险角色',
    seeds: ['gido-adv-a', 'gido-adv-b', 'gido-adv-c', 'gido-adv-d', 'gido-adv-e', 'gido-adv-f'],
    backgrounds: BG_WARM,
  },
  {
    key: 'pixel',
    label: '像素风',
    description: '复古像素头像',
    seeds: ['gido-pixel-a', 'gido-pixel-b', 'gido-pixel-c', 'gido-pixel-d', 'gido-pixel-e', 'gido-pixel-f'],
    backgrounds: BG_VIVID,
  },
  {
    key: 'micah',
    label: '手绘',
    description: '柔和手绘插画',
    seeds: ['gido-micah-a', 'gido-micah-b', 'gido-micah-c', 'gido-micah-d', 'gido-micah-e', 'gido-micah-f'],
    backgrounds: BG_COOL,
  },
]

/** legacy 数字 ID 1-8（兼容旧数据） */
export const AVATAR_LEGACY_PRESET_IDS = ['1', '2', '3', '4', '5', '6', '7', '8'] as const
export type AvatarLegacyPresetId = (typeof AVATAR_LEGACY_PRESET_IDS)[number]

export function stylePresetId(style: AvatarStyleKey, index: number): string {
  return `${style}-${index}`
}

export function listStylePresetIds(style: AvatarStyleKey): string[] {
  return [1, 2, 3, 4, 5, 6].map(i => stylePresetId(style, i))
}

export function allPickerPresetEntries(): { id: string; label: string }[] {
  return AVATAR_STYLE_GROUPS.flatMap(group =>
    listStylePresetIds(group.key).map(id => ({
      id,
      label: `${group.label} ${parseStylePresetIndex(id)?.index ?? ''}`,
    })),
  )
}

export function allBuiltinPresetIds(): string[] {
  const styled = AVATAR_STYLE_GROUPS.flatMap(g => listStylePresetIds(g.key))
  return [...AVATAR_LEGACY_PRESET_IDS, ...styled]
}

export function findStyleGroupForPreset(presetId: string): AvatarStyleGroup | undefined {
  const dash = presetId.indexOf('-')
  if (dash <= 0) return undefined
  const key = presetId.slice(0, dash) as AvatarStyleKey
  return AVATAR_STYLE_GROUPS.find(g => g.key === key)
}

export function parseStylePresetIndex(presetId: string): { style: AvatarStyleKey; index: number } | null {
  const dash = presetId.indexOf('-')
  if (dash <= 0) return null
  const style = presetId.slice(0, dash) as AvatarStyleKey
  const index = Number(presetId.slice(dash + 1))
  if (!AVATAR_STYLE_GROUPS.some(g => g.key === style) || !Number.isInteger(index) || index < 1 || index > 6) {
    return null
  }
  return { style, index }
}
