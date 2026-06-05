/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import SunCalc from 'suncalc'
import type { AppearancePreset } from '../store'
import type { UiThemeId } from './themeIds'

/** 无定位权限时用于日出日落估算（上海附近，东八区可参考） */
export const FALLBACK_COORDS = { lat: 31.2304, lng: 121.4737 }

export function isSolarDaytime(now: Date, lat: number, lng: number): boolean {
  const times = SunCalc.getTimes(now, lat, lng)
  const sr = times.sunrise.getTime()
  const ss = times.sunset.getTime()
  if (!Number.isFinite(sr) || !Number.isFinite(ss)) {
    const h = now.getHours() + now.getMinutes() / 60
    return h >= 6 && h < 18
  }
  const t = now.getTime()
  return t >= sr && t <= ss
}

/**
 * 解析当前应使用的浅色工作台主题 id（供样式与 Ant Design token 使用）。
 * - 自动：日照内 → 清爽经典；日落至日出 → 护眼暖纸（略压蓝光感，仍为浅色）
 * - 手动：固定所选配色
 */
export function resolveUiThemeId(
  preset: AppearancePreset,
  latLng: { lat: number; lng: number } | null,
  now: Date,
): UiThemeId {
  if (preset !== 'auto') return preset
  const { lat, lng } = latLng ?? FALLBACK_COORDS
  return isSolarDaytime(now, lat, lng) ? 'lightClassic' : 'warmPaper'
}
