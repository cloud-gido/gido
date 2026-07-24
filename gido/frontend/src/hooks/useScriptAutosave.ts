/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 统一脚本自动保存：防抖持久化 + 本地兜底 + 状态文案。
 * Studio / Probe / Stream / NodeConfigModal 必须共用此 Hook（见 skill gido-editor-autosave）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  clearScriptLocalDraft,
  writeScriptLocalDraft,
} from '../utils/scriptLocalDraft'

export type ScriptAutosaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error'

export const SCRIPT_AUTOSAVE_DEBOUNCE_MS = 1600

export type UseScriptAutosaveOptions = {
  /** 是否允许自动持久化（持锁、未发布锁定等） */
  enabled: boolean
  /** 相对 baseline 是否有未确认修改 */
  dirty: boolean
  /** 当前编辑器内容 */
  value: string
  /** localStorage key；null 表示不写本地草稿 */
  storageKey: string | null
  /** 静默持久化（服务端草稿或不写版本历史）；Probe 可为写本机权威状态 */
  persist: (script: string) => Promise<void>
  /** 持久化成功且内容仍匹配时回调（父组件清 dirty / 更新 baseline） */
  onSynced?: (script: string) => void
  debounceMs?: number
  /**
   * 页面卸载时的 keepalive 冲刷（可选）。
   * Studio/Stream 可传 fetch keepalive；Probe 可不传。
   */
  persistKeepalive?: (script: string) => void
}

export type UseScriptAutosaveResult = {
  status: ScriptAutosaveStatus
  hint: string
  setStatus: (s: ScriptAutosaveStatus, hint?: string) => void
  /** 立即冲刷当前 dirty 内容 */
  flush: () => Promise<boolean>
  /** beforeunload 尽力冲刷 */
  flushKeepalive: () => void
  /** 显式「保存版本」成功后重置状态文案 */
  markVersionSaved: () => void
}

export function useScriptAutosave(opts: UseScriptAutosaveOptions): UseScriptAutosaveResult {
  const {
    enabled,
    dirty,
    value,
    storageKey,
    persist,
    onSynced,
    debounceMs = SCRIPT_AUTOSAVE_DEBOUNCE_MS,
    persistKeepalive,
  } = opts

  const [status, setStatusState] = useState<ScriptAutosaveStatus>('idle')
  const [hint, setHint] = useState('')
  const seqRef = useRef(0)
  const flushingRef = useRef(false)
  const valueRef = useRef(value)
  const dirtyRef = useRef(dirty)
  const enabledRef = useRef(enabled)
  const persistRef = useRef(persist)
  const onSyncedRef = useRef(onSynced)
  const storageKeyRef = useRef(storageKey)
  const keepaliveRef = useRef(persistKeepalive)

  valueRef.current = value
  dirtyRef.current = dirty
  enabledRef.current = enabled
  persistRef.current = persist
  onSyncedRef.current = onSynced
  storageKeyRef.current = storageKey
  keepaliveRef.current = persistKeepalive

  const setStatus = useCallback((s: ScriptAutosaveStatus, h = '') => {
    setStatusState(s)
    setHint(h)
  }, [])

  const flush = useCallback(async (): Promise<boolean> => {
    if (!enabledRef.current || !dirtyRef.current) return true
    if (flushingRef.current) return false
    const script = valueRef.current
    flushingRef.current = true
    try {
      await persistRef.current(script)
      if (valueRef.current !== script) return true
      clearScriptLocalDraft(storageKeyRef.current)
      onSyncedRef.current?.(script)
      return true
    } catch {
      return false
    } finally {
      flushingRef.current = false
    }
  }, [])

  const flushKeepalive = useCallback(() => {
    if (!enabledRef.current || !dirtyRef.current) return
    const script = valueRef.current
    writeScriptLocalDraft(storageKeyRef.current, script)
    keepaliveRef.current?.(script)
  }, [])

  const markVersionSaved = useCallback(() => {
    clearScriptLocalDraft(storageKeyRef.current)
    setStatus('saved', '已写入版本历史')
  }, [setStatus])

  // 脏内容 → 本地草稿 + 防抖持久化
  useEffect(() => {
    if (!enabled || !dirty) return
    writeScriptLocalDraft(storageKey, value)
    const seq = ++seqRef.current
    setStatus('pending')
    const timer = window.setTimeout(() => {
      void (async () => {
        if (seqRef.current !== seq) return
        if (!enabledRef.current || !dirtyRef.current) return
        if (valueRef.current !== value) return
        setStatus('saving')
        const ok = await flush()
        if (seqRef.current !== seq) return
        if (ok && !dirtyRef.current) {
          setStatus('saved', new Date().toLocaleTimeString())
        } else if (!ok) {
          setStatus('error', '将保留在本地，网络恢复后重试')
        }
      })()
    }, debounceMs)
    return () => window.clearTimeout(timer)
  }, [enabled, dirty, value, storageKey, debounceMs, flush, setStatus])

  useEffect(() => {
    const onBeforeUnload = () => flushKeepalive()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [flushKeepalive])

  return { status, hint, setStatus, flush, flushKeepalive, markVersionSaved }
}
