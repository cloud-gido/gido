/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { BRAND, BRAND_ASSETS, BRAND_DESIGN } from './branding'

/** 变更品牌 favicon 时递增，用于绕过浏览器强缓存 */
export const BRAND_HEAD_VERSION = 'gido20260605'

type LinkSpec = {
  rel: string
  href: string
  type?: string
  sizes?: string
}

/** 仅浅色 SVG favicon，与登录页 GidoBrandHero 星徽一致 */
const HEAD_LINKS: LinkSpec[] = [
  { rel: 'icon', href: BRAND_ASSETS.faviconSvg, type: 'image/svg+xml', sizes: 'any' },
  { rel: 'apple-touch-icon', href: BRAND_ASSETS.appleTouchIcon, type: 'image/svg+xml', sizes: '180x180' },
  { rel: 'manifest', href: BRAND_ASSETS.webManifest },
]

/** 运行时强制写入品牌 title / favicon（解决标签图标缓存不更新） */
export function ensureBrandHead() {
  document.title = `${BRAND.suiteFull} — 开源大数据开发与治理`

  document.querySelectorAll('link[rel*="icon"], link[rel="manifest"]').forEach(el => el.remove())

  for (const spec of HEAD_LINKS) {
    const link = document.createElement('link')
    link.rel = spec.rel
    link.href = `${spec.href}?v=${BRAND_HEAD_VERSION}`
    if (spec.type) link.type = spec.type
    if (spec.sizes) link.sizes = spec.sizes
    document.head.appendChild(link)
  }

  let theme = document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null
  if (!theme) {
    theme = document.createElement('meta')
    theme.name = 'theme-color'
    document.head.appendChild(theme)
  }
  theme.content = BRAND_DESIGN.palette.lightUi
}
