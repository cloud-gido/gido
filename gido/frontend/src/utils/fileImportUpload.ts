/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 本地文件导入：可断点续传的分片上传（localStorage 指纹 + 服务端会话）。
 */
import request from '../api/request'

export const FILE_IMPORT_CHUNK_BYTES = 8 * 1024 * 1024
const CONCURRENCY = 3
const MAX_ATTEMPTS = 5
const SESSION_PREFIX = 'gido.fileImport.session.v1:'

export type FileImportUploadPhase = 'uploading' | 'parsing'

export type FileImportUploadOpts = {
  encoding?: string
  delimiter?: string
  has_header?: boolean
  sheet_name?: string
  onProgress?: (percent: number) => void
  onPhase?: (phase: FileImportUploadPhase) => void
  onStatus?: (info: { received: number; total: number; resumed: boolean; fileId: string }) => void
  signal?: AbortSignal
}

type StoredSession = {
  workspaceId: number
  fileId: string
  clientKey: string
  filename: string
  sizeBytes: number
  totalChunks: number
  chunkBytes: number
  updatedAt: number
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

export function fileImportClientKey(workspaceId: number, file: File): string {
  return `${workspaceId}|${file.name}|${file.size}|${file.lastModified}`
}

function sessionStorageKey(clientKey: string): string {
  return `${SESSION_PREFIX}${clientKey}`
}

export function loadFileImportSession(clientKey: string): StoredSession | null {
  try {
    const raw = localStorage.getItem(sessionStorageKey(clientKey))
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredSession
    if (!parsed?.fileId) return null
    // 7 天过期
    if (Date.now() - (parsed.updatedAt || 0) > 7 * 24 * 3600 * 1000) {
      clearFileImportSession(clientKey)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function saveFileImportSession(session: StoredSession) {
  try {
    localStorage.setItem(
      sessionStorageKey(session.clientKey),
      JSON.stringify({ ...session, updatedAt: Date.now() }),
    )
  } catch {
    /* quota / private mode */
  }
}

export function clearFileImportSession(clientKey: string) {
  try {
    localStorage.removeItem(sessionStorageKey(clientKey))
  } catch {
    /* ignore */
  }
}

function assertNotAborted(signal?: AbortSignal) {
  if (signal?.aborted) {
    const err = new Error('上传已取消')
    ;(err as any).code = 'ERR_CANCELED'
    throw err
  }
}

async function mapPool<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  const queue = [...items]
  const runners = Array.from({ length: Math.min(limit, Math.max(1, queue.length)) }, async () => {
    while (queue.length) {
      const item = queue.shift()
      if (item === undefined) return
      await worker(item)
    }
  })
  await Promise.all(runners)
}

export async function uploadFileImportResumable(
  workspaceId: number,
  file: File,
  opts: FileImportUploadOpts = {},
): Promise<any> {
  const chunkBytes = FILE_IMPORT_CHUNK_BYTES
  const totalChunks = Math.max(1, Math.ceil(file.size / chunkBytes))
  const clientKey = fileImportClientKey(workspaceId, file)
  assertNotAborted(opts.signal)

  const init: any = await request.post('/integration/file-import/upload-init', {
    workspace_id: workspaceId,
    filename: file.name,
    size_bytes: file.size,
    total_chunks: totalChunks,
    client_key: clientKey,
    chunk_bytes: chunkBytes,
  })

  const fileId = String(init.file_id)
  const resumed = !!init.resumed
  saveFileImportSession({
    workspaceId,
    fileId,
    clientKey,
    filename: file.name,
    sizeBytes: file.size,
    totalChunks,
    chunkBytes,
    updatedAt: Date.now(),
  })

  // 以服务端 status 为准拿 missing（init 已带；再拉一次更稳）
  let status: any = init
  try {
    status = await request.get('/integration/file-import/upload-status', {
      params: { workspace_id: workspaceId, file_id: fileId },
    })
  } catch {
    /* use init */
  }

  const missing: number[] = Array.isArray(status.missing_chunks)
    ? status.missing_chunks
    : Array.from({ length: totalChunks }, (_, i) => i)
  const already = totalChunks - missing.length
  opts.onPhase?.('uploading')
  opts.onStatus?.({ received: already, total: totalChunks, resumed, fileId })
  opts.onProgress?.(Math.min(99, Math.round((already / totalChunks) * 100)))

  let doneCount = already
  const bump = () => {
    doneCount += 1
    opts.onProgress?.(Math.min(99, Math.round((doneCount / totalChunks) * 100)))
    opts.onStatus?.({ received: doneCount, total: totalChunks, resumed, fileId })
  }

  const uploadOne = async (index: number, attempt = 1): Promise<void> => {
    assertNotAborted(opts.signal)
    const start = index * chunkBytes
    const end = Math.min(file.size, start + chunkBytes)
    const blob = file.slice(start, end)
    const fd = new FormData()
    fd.append('workspace_id', String(workspaceId))
    fd.append('file_id', fileId)
    fd.append('chunk_index', String(index))
    fd.append('file', blob, `${file.name}.part${index}`)
    try {
      await request.post('/integration/file-import/upload-chunk', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 0,
        signal: opts.signal,
      })
    } catch (e: any) {
      assertNotAborted(opts.signal)
      if (attempt >= MAX_ATTEMPTS) throw e
      await sleep(Math.min(8000, 400 * 2 ** (attempt - 1)))
      return uploadOne(index, attempt + 1)
    }
  }

  await mapPool(missing, CONCURRENCY, async (idx) => {
    await uploadOne(idx)
    bump()
    saveFileImportSession({
      workspaceId,
      fileId,
      clientKey,
      filename: file.name,
      sizeBytes: file.size,
      totalChunks,
      chunkBytes,
      updatedAt: Date.now(),
    })
  })

  assertNotAborted(opts.signal)
  opts.onPhase?.('parsing')
  opts.onProgress?.(99)

  const completeFd = new FormData()
  completeFd.append('workspace_id', String(workspaceId))
  completeFd.append('file_id', fileId)
  if (opts.encoding) completeFd.append('encoding', opts.encoding)
  if (opts.delimiter != null) completeFd.append('delimiter', opts.delimiter)
  if (opts.has_header != null) completeFd.append('has_header', String(opts.has_header))
  if (opts.sheet_name) completeFd.append('sheet_name', opts.sheet_name)

  try {
    const result = await request.post('/integration/file-import/upload-complete', completeFd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      signal: opts.signal,
    })
    clearFileImportSession(clientKey)
    return result
  } catch (e) {
    // 解析失败仍保留会话，便于仅重试 complete；分片已齐
    throw e
  }
}

export async function abortFileImportUpload(workspaceId: number, fileId: string, clientKey?: string) {
  const fd = new FormData()
  fd.append('workspace_id', String(workspaceId))
  fd.append('file_id', fileId)
  try {
    await request.post('/integration/file-import/upload-abort', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  } finally {
    if (clientKey) clearFileImportSession(clientKey)
  }
}

export function describeUploadNetworkError(e: any): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const msg = String(e?.message || '')
  const code = String(e?.code || '')
  if (code === 'ERR_CANCELED' || /上传已取消|canceled|aborted/i.test(msg)) {
    return '上传已取消。未完成的分片仍保留在服务器，可再次选择同一文件继续传。'
  }
  if (
    code === 'ERR_NETWORK'
    || /ERR_HTTP2_PING_FAILED|ERR_CONNECTION_|Network Error|Failed to fetch|timeout/i.test(msg)
    || (!e?.response && msg)
  ) {
    return '网络中断，已保存上传进度。请重新选择同一文件即可断点续传（同名同大小）。'
  }
  return msg || '上传失败'
}
