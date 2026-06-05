/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useAppStore } from '../store'
import { FALLBACK_COORDS, resolveUiThemeId } from '../appearance/resolveAppearanceTheme'
import { antdThemeForUi } from '../appearance/antdThemeByUi'

export default function ShellThemeProvider({ children }: { children: ReactNode }) {
  const appearancePreset = useAppStore(s => s.appearancePreset)
  const appearanceLatLng = useAppStore(s => s.appearanceLatLng)
  const setAppearanceLatLng = useAppStore(s => s.setAppearanceLatLng)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (appearancePreset !== 'auto') return
    const id = window.setInterval(() => setTick(t => t + 1), 60_000)
    return () => window.clearInterval(id)
  }, [appearancePreset])

  useEffect(() => {
    const onFocus = () => {
      if (appearancePreset === 'auto') setTick(t => t + 1)
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [appearancePreset])

  useEffect(() => {
    if (appearanceLatLng != null) return
    if (!('geolocation' in navigator)) {
      setAppearanceLatLng(FALLBACK_COORDS.lat, FALLBACK_COORDS.lng)
      return
    }
    navigator.geolocation.getCurrentPosition(
      p => setAppearanceLatLng(p.coords.latitude, p.coords.longitude),
      () => setAppearanceLatLng(FALLBACK_COORDS.lat, FALLBACK_COORDS.lng),
      { enableHighAccuracy: false, maximumAge: 86_400_000, timeout: 10_000 },
    )
  }, [appearanceLatLng, setAppearanceLatLng])

  const uiThemeId = useMemo(
    () => resolveUiThemeId(appearancePreset, appearanceLatLng, new Date()),
    [appearancePreset, appearanceLatLng, tick],
  )

  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-dw-theme', uiThemeId)
  }, [uiThemeId])

  const antdTheme = useMemo(() => antdThemeForUi(uiThemeId), [uiThemeId])

  return (
    <ConfigProvider locale={zhCN} theme={antdTheme}>
      {children}
    </ConfigProvider>
  )
}
