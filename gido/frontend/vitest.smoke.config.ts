/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Production-bundle smoke only. Requires `npm run build` first.
 */
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'node',
    include: ['src/components/studioWorkbenchSystemSmoke.test.ts'],
    setupFiles: ['src/test/setup.ts'],
  },
})
