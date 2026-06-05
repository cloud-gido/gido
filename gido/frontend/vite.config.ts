/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // 与 Docker 前端 3002 错开，避免本地 dev 覆盖容器导致/about 看不到最新构建
    port: 3003,
    // 浏览器访问 /api/* → 转发到本机后端；须与 uvicorn 端口一致
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
    },
  },
})
