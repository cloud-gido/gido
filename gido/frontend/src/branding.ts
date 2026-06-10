/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export type ProductId = 'batch' | 'stream' | 'service'

export const BRAND_ASSETS = {
  /** 星座轨道星徽（favicon / 启动器 / 侧栏小标） */
  mark: '/brand/gido-mark.svg',
  /** 官方横版 Logo（深色营销物料；站内浅底 UI 请用 mark / GidoBrandHero） */
  logo: '/brand/gido-logo.png',
  /** 矢量横版（文档 / 无 PNG 环境备用） */
  logoSvg: '/brand/gido-logo.svg',
  /** 浏览器标签 / 书签（浅色小清新，与登录页星徽一致；勿用黑底 PNG/ICO） */
  favicon: '/favicon.svg',
  faviconSvg: '/favicon.svg',
  appleTouchIcon: '/apple-touch-icon.svg',
  webManifest: '/site.webmanifest',
  batchMark: '/brand/gido-batch-mark.svg',
  streamMark: '/brand/gido-stream-mark.svg',
  serviceMark: '/brand/gido-service-mark.svg',
} as const

/** 官方视觉语言（与 docs/BRAND.md 一致） */
export const BRAND_DESIGN = {
  motif: '动态星座轨道 · 橙金→电蓝渐变 · 天玑主星',
  palette: {
    orange: '#ff8c00',
    gold: '#ffb347',
    cyan: '#38bdf8',
    blue: '#2563eb',
    navy: '#0a0f1a',
    lightUi: '#eff6ff',
  },
} as const

export const BRAND = {
  suite: '玑渡',
  suiteEn: 'GIDO',
  suiteFull: '玑渡 GIDO',
  suiteLower: 'gido',
  tagline: 'DATA · FLOW · VALUE',
  taglineZh: '璇玑指引 · 数据有渡',
  pitch: '开源大数据开发、调度与数据服务套件',
  offline: 'GIDO Batch',
  realtime: 'GIDO Stream',
  service: 'GIDO Serve',
  offlineShort: '玑渡·批',
  realtimeShort: '玑渡·流',
  serviceShort: '玑渡·服',
  offlineDesc: '离线批处理',
  realtimeDesc: '实时流计算',
  serviceDesc: '数据服务',
} as const

/** 开源与许可证（登录页、关于等） */
export const OPEN_SOURCE = {
  license: 'Apache-2.0',
  licenseUrl: 'https://www.apache.org/licenses/LICENSE-2.0',
  version: '1.0.0',
  /** 关于页内容版本（改维护者等信息时递增，便于确认前端已更新） */
  aboutRevision: '20260611a',
  repositoryUrl: 'https://github.com/cloud-gido/gido',
  docPaths: {
    license: 'LICENSE',
    notice: 'NOTICE',
    trademark: 'TRADEMARK.md',
    security: 'SECURITY.md',
    contributing: 'CONTRIBUTING.md',
    changelog: 'CHANGELOG.md',
    openSource: 'gido/docs/OPEN_SOURCE.md',
    brand: 'gido/docs/BRAND.md',
  },
  /** 项目维护者（关于页、SECURITY.md、README 对齐） */
  maintainers: [
    { name: 'Troy', email: 'troyzhujingbin@163.com' },
    { name: 'Chenghap', email: 'chenghap0712@gmail.com' },
  ],
} as const

/** GitHub 仓库文档链接（关于页、README 对齐） */
export function repoDocUrl(path: string) {
  return `${OPEN_SOURCE.repositoryUrl}/blob/main/${path}`
}

/** 侧栏 / 登录卡片 / 产品启动器共用元数据 */
export const PRODUCT_SHELL_META: Record<
  ProductId,
  { title: string; subtitle: string; tagline: string; desc: string }
> = {
  batch: {
    title: BRAND.offline,
    subtitle: BRAND.offlineShort,
    tagline: '离线编排 · 调度渡送',
    desc: BRAND.offlineDesc,
  },
  stream: {
    title: BRAND.realtime,
    subtitle: BRAND.realtimeShort,
    tagline: '实时流转 · Flink 引擎',
    desc: BRAND.realtimeDesc,
  },
  service: {
    title: BRAND.service,
    subtitle: BRAND.serviceShort,
    tagline: '数据出渡 · API 网关',
    desc: BRAND.serviceDesc,
  },
}

export const PRODUCT_MARK_LABEL: Record<ProductId, string> = {
  batch: BRAND.offline,
  stream: BRAND.realtime,
  service: BRAND.service,
}

/** 三件套并列展示（登录副标题、关于页等） */
export function productSuiteLine(sep = ' · ') {
  return [BRAND.offlineShort, BRAND.realtimeShort, BRAND.serviceShort].join(sep)
}
