/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { theme as antTheme } from 'antd'
import type { ThemeConfig } from 'antd'
import type { UiThemeId } from './themeIds'

const sharedComponents: ThemeConfig['components'] = {
  Layout: {
    headerHeight: 56,
    headerPadding: '0 24px',
  },
  Menu: {
    itemBorderRadius: 8,
    iconSize: 16,
  },
}

export function antdThemeForUi(ui: UiThemeId): ThemeConfig {
  const defs: Record<UiThemeId, ThemeConfig> = {
    lightClassic: {
      algorithm: antTheme.defaultAlgorithm,
      token: {
        colorPrimary: '#2563eb',
        colorBgLayout: '#eef1f7',
        colorBgContainer: '#ffffff',
        colorBorder: '#e8ecf2',
        borderRadius: 8,
      },
      components: {
        ...sharedComponents,
        Layout: {
          ...sharedComponents.Layout,
          bodyBg: '#eef1f7',
          headerBg: '#ffffff',
        },
      },
    },
    warmPaper: {
      algorithm: antTheme.defaultAlgorithm,
      token: {
        colorPrimary: '#2563eb',
        colorBgLayout: '#f5f2ec',
        colorBgContainer: '#fcfbf8',
        colorBorder: '#e6e1d8',
        borderRadius: 8,
      },
      components: {
        ...sharedComponents,
        Layout: {
          ...sharedComponents.Layout,
          bodyBg: '#f5f2ec',
          headerBg: '#fdfcfa',
        },
      },
    },
    coolMist: {
      algorithm: antTheme.defaultAlgorithm,
      token: {
        colorPrimary: '#2563eb',
        colorBgLayout: '#e8eef8',
        colorBgContainer: '#ffffff',
        colorBorder: '#dde4f0',
        borderRadius: 8,
      },
      components: {
        ...sharedComponents,
        Layout: {
          ...sharedComponents.Layout,
          bodyBg: '#e8eef8',
          headerBg: '#fafcff',
        },
      },
    },
    eveningMist: {
      algorithm: antTheme.defaultAlgorithm,
      token: {
        colorPrimary: '#2563eb',
        colorBgLayout: '#e4e9f4',
        colorBgContainer: '#f7f8fd',
        colorBorder: '#d5dbe8',
        borderRadius: 8,
      },
      components: {
        ...sharedComponents,
        Layout: {
          ...sharedComponents.Layout,
          bodyBg: '#e4e9f4',
          headerBg: '#eff1f9',
        },
      },
    },
    mintWater: {
      algorithm: antTheme.defaultAlgorithm,
      token: {
        colorPrimary: '#0891b2',
        colorBgLayout: '#eaf4f5',
        colorBgContainer: '#fbfeff',
        colorBorder: '#cde8e9',
        borderRadius: 8,
      },
      components: {
        ...sharedComponents,
        Layout: {
          ...sharedComponents.Layout,
          bodyBg: '#eaf4f5',
          headerBg: '#f6fcfc',
        },
      },
    },
  }
  return defs[ui]
}
