/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { create } from 'zustand'
import { isUiThemeId, type UiThemeId } from '../appearance/themeIds'

const LS_APPEARANCE_PRESET = 'dw-appearance-preset'
/** 兼容旧键名 */
const LS_APPEARANCE_LEGACY = 'dw-appearance-mode'
const LS_APPEARANCE_LAT = 'dw-appearance-lat'
const LS_APPEARANCE_LNG = 'dw-appearance-lng'

export type AppearancePreset = 'auto' | UiThemeId

function loadAppearancePreset(): AppearancePreset {
  try {
    const v = localStorage.getItem(LS_APPEARANCE_PRESET) ?? localStorage.getItem(LS_APPEARANCE_LEGACY)
    if (v === 'auto') return 'auto'
    if (isUiThemeId(v)) return v
    if (v === 'light') return 'lightClassic'
    if (v === 'dark') return 'warmPaper'
  } catch { /* noop */ }
  return 'auto'
}

function loadAppearanceLatLng(): { lat: number; lng: number } | null {
  try {
    const la = localStorage.getItem(LS_APPEARANCE_LAT)
    const ln = localStorage.getItem(LS_APPEARANCE_LNG)
    if (la == null || ln == null) return null
    const lat = Number(la)
    const lng = Number(ln)
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
    return { lat, lng }
  } catch { /* noop */ }
  return null
}

interface AppState {
  user: any
  currentWorkspace: any
  pendingOpenNodeId: number | null
  appearancePreset: AppearancePreset
  appearanceLatLng: { lat: number; lng: number } | null
  setUser: (user: any) => void
  setCurrentWorkspace: (ws: any) => void
  setPendingOpenNodeId: (id: number | null) => void
  setAppearancePreset: (preset: AppearancePreset) => void
  setAppearanceLatLng: (lat: number, lng: number) => void
  logout: () => void
}

export const useAppStore = create<AppState>(set => ({
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  currentWorkspace: JSON.parse(localStorage.getItem('workspace') || 'null'),
  pendingOpenNodeId: null,
  appearancePreset: loadAppearancePreset(),
  appearanceLatLng: loadAppearanceLatLng(),
  setUser: (user) => {
    localStorage.setItem('user', JSON.stringify(user))
    set({ user })
  },
  setCurrentWorkspace: (ws) => {
    localStorage.setItem('workspace', JSON.stringify(ws))
    set({ currentWorkspace: ws })
  },
  setPendingOpenNodeId: (id) => set({ pendingOpenNodeId: id }),
  setAppearancePreset: (preset) => {
    try {
      localStorage.setItem(LS_APPEARANCE_PRESET, preset)
      localStorage.removeItem(LS_APPEARANCE_LEGACY)
    } catch { /* noop */ }
    set({ appearancePreset: preset })
  },
  setAppearanceLatLng: (lat, lng) => {
    try {
      localStorage.setItem(LS_APPEARANCE_LAT, String(lat))
      localStorage.setItem(LS_APPEARANCE_LNG, String(lng))
    } catch { /* noop */ }
    set({ appearanceLatLng: { lat, lng } })
  },
  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('workspace')
    set({ user: null, currentWorkspace: null })
  },
}))
