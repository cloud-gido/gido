/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { createAvatar } from '@dicebear/core'
import {
  funEmoji,
  lorelei,
  notionists,
  bigSmile,
  avataaars,
  personas,
  bottts,
  adventurer,
  pixelArt,
  micah,
} from '@dicebear/collection'
import {
  AVATAR_STYLE_GROUPS,
  parseStylePresetIndex,
  type AvatarLegacyPresetId,
  type AvatarStyleKey,
} from './avatarCatalog'

type PresetSpec = {
  seed: string
  backgroundColor: string
  style: AvatarStyleKey
}

/** legacy 1-8，保持与旧版一致 */
const LEGACY_PRESET_SPECS: Record<AvatarLegacyPresetId, PresetSpec> = {
  '1': { style: 'emoji', seed: 'gido-sunny', backgroundColor: 'fef3c7' },
  '2': { style: 'lorelei', seed: 'gido-forest', backgroundColor: 'd1fae5' },
  '3': { style: 'notion', seed: 'gido-ocean', backgroundColor: 'dbeafe' },
  '4': { style: 'smile', seed: 'gido-lavender', backgroundColor: 'ede9fe' },
  '5': { style: 'emoji', seed: 'gido-coral', backgroundColor: 'ffe4e6' },
  '6': { style: 'lorelei', seed: 'gido-sky', backgroundColor: 'cffafe' },
  '7': { style: 'notion', seed: 'gido-peach', backgroundColor: 'ffedd5' },
  '8': { style: 'smile', seed: 'gido-slate', backgroundColor: 'e2e8f0' },
}

const svgCache = new Map<string, string>()

function resolvePresetSpec(presetId: string): PresetSpec | null {
  if (presetId in LEGACY_PRESET_SPECS) {
    return LEGACY_PRESET_SPECS[presetId as AvatarLegacyPresetId]
  }
  const parsed = parseStylePresetIndex(presetId)
  if (!parsed) return null
  const group = AVATAR_STYLE_GROUPS.find(g => g.key === parsed.style)
  if (!group) return null
  return {
    style: parsed.style,
    seed: group.seeds[parsed.index - 1],
    backgroundColor: group.backgrounds[parsed.index - 1],
  }
}

function renderPreset(spec: PresetSpec, size: number): string {
  const options = {
    seed: spec.seed,
    size,
    backgroundColor: [spec.backgroundColor],
  }
  switch (spec.style) {
    case 'lorelei':
      return createAvatar(lorelei, options).toString()
    case 'notion':
      return createAvatar(notionists, options).toString()
    case 'smile':
      return createAvatar(bigSmile, options).toString()
    case 'avataaars':
      return createAvatar(avataaars, options).toString()
    case 'personas':
      return createAvatar(personas, options).toString()
    case 'bottts':
      return createAvatar(bottts, options).toString()
    case 'adventurer':
      return createAvatar(adventurer, options).toString()
    case 'pixel':
      return createAvatar(pixelArt, options).toString()
    case 'micah':
      return createAvatar(micah, options).toString()
    default:
      return createAvatar(funEmoji, options).toString()
  }
}

export function presetAvatarSvg(presetId: string, size = 128): string | null {
  const cached = svgCache.get(presetId)
  if (cached) return cached

  const spec = resolvePresetSpec(presetId)
  if (!spec) return null

  const svg = renderPreset(spec, size)
  svgCache.set(presetId, svg)
  return svg
}

export function presetAvatarDataUri(presetId: string, size = 128): string | null {
  const svg = presetAvatarSvg(presetId, size)
  if (!svg) return null
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}
