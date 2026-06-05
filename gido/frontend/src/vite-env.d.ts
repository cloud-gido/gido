/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_ORIGIN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
