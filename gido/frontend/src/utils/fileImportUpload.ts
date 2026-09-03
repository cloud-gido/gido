/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 本地文件导入：可断点续传的分片上传（localStorage 指纹 + 服务端会话）。
 */
import request from '../api/request'

export const FILE_IMPORT_CHUNK_BYTES = 16 * 1024 * 1024
const CONCURRENCY = 6
const MAX_ATTEMPTS = 5
const COMPLETE_GAP_ROUNDS = 3
const SESSION_PREFIX = 'gido.fileImport.session.v1:'

export type FileImportUploadPhase = 'uploading' | 'parsing'

export type FileImportProgressMeta = {
  loaded: number
  total: number
  /** 平滑后的上传速率（字节/秒）；不足样本时为 0 */
  speedBps: number
  /** 预计剩余秒数；速率过低时为 null */
  etaSeconds: number | null
}

export type FileImportUploadOpts = {
  encoding?: string
  delimiter?: string
  has_header?: boolean
  sheet_name?: string
  onProgress?: (percent: number, meta?: FileImportProgressMeta) => void
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

/** 预计剩余时间文案，如「约 2 分 30 秒」；无法估计时返回空串 */
export function formatUploadEta(etaSeconds: number | null | undefined): string {
  if (etaSeconds == null || !Number.isFinite(etaSeconds) || etaSeconds < 0) return ''
  const sec = Math.max(0, Math.ceil(etaSeconds))
  if (sec < 5) return '即将完成'
  if (sec < 60) return `约 ${sec} 秒`
  const min = Math.floor(sec / 60)
  const rem = sec % 60
  if (min < 60) {
    return rem > 0 ? `约 ${min} 分 ${rem} 秒` : `约 ${min} 分钟`
  }
  const hr = Math.floor(min / 60)
  const minRem = min % 60
  return minRem > 0 ? `约 ${hr} 小时 ${minRem} 分` : `约 ${hr} 小时`
}

export function formatUploadSpeed(speedBps: number | null | undefined): string {
  if (speedBps == null || !Number.isFinite(speedBps) || speedBps < 256) return ''
  if (speedBps >= 1024 * 1024) return `${(speedBps / (1024 * 1024)).toFixed(1)} MB/s`
  if (speedBps >= 1024) return `${Math.round(speedBps / 1024)} KB/s`
  return `${Math.round(speedBps)} B/s`
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

function isSessionGoneError(e: any): boolean {
  const status = e?.response?.status
  const detail = String(e?.response?.data?.detail || e?.message || '')
  return status === 404 && /不存在或已过期|不属于该工作空间/.test(detail)
}

function isIncompleteChunksError(e: any): boolean {
  const detail = String(e?.response?.data?.detail || e?.message || '')
  return e?.response?.status === 400 && /分片不完整|缺失.*片/.test(detail)
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

async function fetchUploadStatus(workspaceId: number, fileId: string) {
  return request.get('/integration/file-import/upload-status', {
    params: { workspace_id: workspaceId, file_id: fileId },
  })
}

async function runChunkedUpload(
  workspaceId: number,
  file: File,
  opts: FileImportUploadOpts,
  forceNew: boolean,
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
    force_new: forceNew,
  })

  const fileId = String(init.file_id)
  const resumed = !!init.resumed && !forceNew
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

  let status: any = init
  try {
    status = await fetchUploadStatus(workspaceId, fileId)
  } catch (e: any) {
    if (isSessionGoneError(e)) throw e
    /* use init */
  }

  let missing: number[] = Array.isArray(status.missing_chunks)
    ? status.missing_chunks
    : Array.from({ length: totalChunks }, (_, i) => i)
  const already = totalChunks - missing.length
  opts.onPhase?.('uploading')
  opts.onStatus?.({ received: already, total: totalChunks, resumed, fileId })

  let doneCount = already
  let completedBytes = 0
  for (let i = 0; i < totalChunks; i += 1) {
    if (!missing.includes(i)) {
      const start = i * chunkBytes
      completedBytes += Math.min(chunkBytes, file.size - start)
    }
  }
  const inflightLoaded = new Map<number, number>()
  let speedEwma = 0
  let lastSampleAt = Date.now()
  let lastSampleLoaded = completedBytes

  const reportByteProgress = () => {
    let inflight = 0
    inflightLoaded.forEach((n) => { inflight += n })
    const loaded = Math.min(file.size, completedBytes + inflight)
    const now = Date.now()
    const dt = (now - lastSampleAt) / 1000
    if (dt >= 0.4) {
      const delta = loaded - lastSampleLoaded
      if (delta >= 0) {
        const instant = delta / dt
        speedEwma = speedEwma > 0 ? speedEwma * 0.72 + instant * 0.28 : instant
      }
      lastSampleAt = now
      lastSampleLoaded = loaded
    }
    const remain = Math.max(0, file.size - loaded)
    const etaSeconds = speedEwma >= 8 * 1024 ? Math.ceil(remain / speedEwma) : null
    const pct = file.size > 0 ? Math.min(99, Math.round((loaded / file.size) * 100)) : 0
    opts.onProgress?.(pct, {
      loaded,
      total: file.size,
      speedBps: speedEwma,
      etaSeconds,
    })
    opts.onStatus?.({
      received: Math.min(totalChunks, doneCount),
      total: totalChunks,
      resumed,
      fileId,
    })
  }

  const bump = (chunkSize: number) => {
    doneCount += 1
    completedBytes += chunkSize
    reportByteProgress()
  }

  const uploadOne = async (index: number, attempt = 1): Promise<number> => {
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
      // 勿手动设 Content-Type：须由浏览器带 multipart boundary，否则分片会一直 pending
      await request.post('/integration/file-import/upload-chunk', fd, {
        timeout: 0,
        signal: opts.signal,
        onUploadProgress: (evt) => {
          inflightLoaded.set(index, evt.loaded || 0)
          reportByteProgress()
        },
      })
      inflightLoaded.delete(index)
      return blob.size
    } catch (e: any) {
      inflightLoaded.delete(index)
      reportByteProgress()
      assertNotAborted(opts.signal)
      if (isSessionGoneError(e)) throw e
      if (attempt >= MAX_ATTEMPTS) throw e
      await sleep(Math.min(8000, 400 * 2 ** (attempt - 1)))
      return uploadOne(index, attempt + 1)
    }
  }

  const uploadMissing = async (indices: number[]) => {
    if (!indices.length) return
    await mapPool(indices, CONCURRENCY, async (idx) => {
      const size = await uploadOne(idx)
      bump(size)
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
  }

  reportByteProgress()
  await uploadMissing(missing)

  // complete 前以服务端 status 为准补齐漏片（并发 / 多副本对账延迟）
  for (let round = 0; round < COMPLETE_GAP_ROUNDS; round += 1) {
    assertNotAborted(opts.signal)
    let latest: any
    try {
      latest = await fetchUploadStatus(workspaceId, fileId)
    } catch (e: any) {
      if (isSessionGoneError(e)) throw e
      break
    }
    const stillMissing: number[] = Array.isArray(latest.missing_chunks) ? latest.missing_chunks : []
    if (!stillMissing.length) break
    opts.onPhase?.('uploading')
    const missingSet = new Set(stillMissing)
    doneCount = Math.max(0, totalChunks - stillMissing.length)
    completedBytes = 0
    for (let i = 0; i < totalChunks; i += 1) {
      if (!missingSet.has(i)) {
        const start = i * chunkBytes
        completedBytes += Math.min(chunkBytes, file.size - start)
      }
    }
    reportByteProgress()
    await uploadMissing(stillMissing)
  }

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

  const postComplete = () => request.post('/integration/file-import/upload-complete', completeFd, {
    timeout: 0,
    signal: opts.signal,
  })

  try {
    const result = await postComplete()
    clearFileImportSession(clientKey)
    return result
  } catch (e: any) {
    if (!isIncompleteChunksError(e)) throw e
    // complete 仍报缺片：再补一轮后重试一次合并
    opts.onPhase?.('uploading')
    try {
      const latest: any = await fetchUploadStatus(workspaceId, fileId)
      const stillMissing: number[] = Array.isArray(latest.missing_chunks) ? latest.missing_chunks : []
      if (stillMissing.length) {
        const missingSet = new Set(stillMissing)
        doneCount = Math.max(0, totalChunks - stillMissing.length)
        completedBytes = 0
        for (let i = 0; i < totalChunks; i += 1) {
          if (!missingSet.has(i)) {
            const start = i * chunkBytes
            completedBytes += Math.min(chunkBytes, file.size - start)
          }
        }
        reportByteProgress()
        await uploadMissing(stillMissing)
      }
    } catch (inner: any) {
      if (isSessionGoneError(inner)) throw inner
      throw e
    }
    opts.onPhase?.('parsing')
    const result = await postComplete()
    clearFileImportSession(clientKey)
    return result
  }
}

export async function uploadFileImportResumable(
  workspaceId: number,
  file: File,
  opts: FileImportUploadOpts = {},
): Promise<any> {
  const clientKey = fileImportClientKey(workspaceId, file)
  try {
    return await runChunkedUpload(workspaceId, file, opts, false)
  } catch (e: any) {
    if (!isSessionGoneError(e)) throw e
    // 多副本空会话 / 过期 file_id：清本地缓存后强制新建会话再传
    clearFileImportSession(clientKey)
    opts.onProgress?.(0)
    return runChunkedUpload(workspaceId, file, opts, true)
  }
}

export async function abortFileImportUpload(workspaceId: number, fileId: string, clientKey?: string) {
  const fd = new FormData()
  fd.append('workspace_id', String(workspaceId))
  fd.append('file_id', fileId)
  try {
    await request.post('/integration/file-import/upload-abort', fd, {
      timeout: 60000,
    })
  } finally {
    if (clientKey) clearFileImportSession(clientKey)
  }
}

export function describeUploadNetworkError(e: any): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    if (/不存在或已过期/.test(detail)) {
      return '上传会话已失效（多副本或过期）。请再次选择同一文件重新开始上传。'
    }
    if (/分片不完整|缺失.*片/.test(detail)) {
      return `${detail} 请再次选择同一文件继续补传缺失分片。`
    }
    return detail
  }
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
