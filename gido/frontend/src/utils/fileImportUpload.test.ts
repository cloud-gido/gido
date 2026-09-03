/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearFileImportSession,
  describeUploadNetworkError,
  fileImportClientKey,
  formatUploadEta,
  formatUploadSpeed,
  loadFileImportSession,
  saveFileImportSession,
} from './fileImportUpload'

const store = new Map<string, string>()

describe('fileImportUpload resume helpers', () => {
  beforeEach(() => {
    store.clear()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => { store.set(k, v) },
      removeItem: (k: string) => { store.delete(k) },
      clear: () => store.clear(),
    })
  })

  it('builds stable client key from file identity', () => {
    const file = new File(['abc'], 'a.csv', { type: 'text/csv', lastModified: 123 })
    expect(fileImportClientKey(7, file)).toBe('7|a.csv|3|123')
  })

  it('persists and clears upload session', () => {
    const key = '7|a.csv|3|123'
    saveFileImportSession({
      workspaceId: 7,
      fileId: 'deadbeef'.repeat(4),
      clientKey: key,
      filename: 'a.csv',
      sizeBytes: 3,
      totalChunks: 1,
      chunkBytes: 8 * 1024 * 1024,
      updatedAt: Date.now(),
    })
    expect(loadFileImportSession(key)?.fileId).toBe('deadbeef'.repeat(4))
    clearFileImportSession(key)
    expect(loadFileImportSession(key)).toBeNull()
  })

  it('maps network errors to resume tip', () => {
    expect(describeUploadNetworkError({ code: 'ERR_NETWORK', message: 'Network Error' }))
      .toContain('断点续传')
    expect(describeUploadNetworkError({ message: 'ERR_HTTP2_PING_FAILED' }))
      .toContain('断点续传')
    expect(describeUploadNetworkError({ code: 'ERR_CANCELED', message: 'canceled' }))
      .toContain('取消')
  })

  it('formats upload eta and speed', () => {
    expect(formatUploadEta(null)).toBe('')
    expect(formatUploadEta(3)).toBe('即将完成')
    expect(formatUploadEta(45)).toBe('约 45 秒')
    expect(formatUploadEta(150)).toBe('约 2 分 30 秒')
    expect(formatUploadSpeed(0)).toBe('')
    expect(formatUploadSpeed(50 * 1024)).toContain('KB/s')
    expect(formatUploadSpeed(2.5 * 1024 * 1024)).toContain('MB/s')
  })
})
