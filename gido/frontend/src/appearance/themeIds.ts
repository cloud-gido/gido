/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export const UI_THEME_IDS = ['lightClassic', 'warmPaper', 'coolMist', 'eveningMist', 'mintWater'] as const

export type UiThemeId = (typeof UI_THEME_IDS)[number]

export function isUiThemeId(s: string): s is UiThemeId {
  return (UI_THEME_IDS as readonly string[]).includes(s)
}
