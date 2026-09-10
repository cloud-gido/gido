/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 从路径解析当前子产品；空间壳据此切换侧栏/强调色，不改 currentWorkspace。
 */
import { R, type ProductId } from '../../routes'

export function productFromPath(pathname: string): ProductId {
  const p = pathname.replace(/\/+$/, '') || '/'
  if (p === R.stream.root || p.startsWith(`${R.stream.root}/`)) return 'stream'
  if (p === R.service.root || p.startsWith(`${R.service.root}/`)) return 'service'
  return 'batch'
}
