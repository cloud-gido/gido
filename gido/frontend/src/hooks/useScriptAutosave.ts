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
  /**
   * 当前编辑实体 id（节点/作业/探查脚本）。
   * 用于避免切 Tab/作业后，迟到的 onSynced 误清新实体的 dirty。
   */
  entityId?: string | number | null
  /** 静默持久化；第二参为发起保存时的 entityId（勿用最新选中项，防串写） */
  persist: (script: string, entityId: string | number | null | undefined) => Promise<void>
  /** 持久化成功且实体/内容仍匹配时回调（父组件清 dirty / 更新 baseline） */
  onSynced?: (script: string, entityId: string | number | null | undefined) => void
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
  /**
   * 相对上次「保存版本」是否仍有未记入历史的改动。
   * 与草稿 dirty 解耦：静默 autosave 成功后仍可为 true，避免「保存版本 *」随草稿闪烁。
   */
  versionDirty: boolean
  setStatus: (s: ScriptAutosaveStatus, hint?: string) => void
  /** 立即冲刷当前 dirty 内容 */
  flush: () => Promise<boolean>
  /** beforeunload 尽力冲刷 */
  flushKeepalive: () => void
  /** 显式「保存版本」成功后重置版本脏标记与状态 */
  markVersionSaved: () => void
}

async function waitWhile(cond: () => boolean, maxMs: number, stepMs = 40): Promise<boolean> {
  const deadline = Date.now() + maxMs
  while (cond()) {
    if (Date.now() >= deadline) return false
    await new Promise(r => window.setTimeout(r, stepMs))
  }
  return true
}

export function useScriptAutosave(opts: UseScriptAutosaveOptions): UseScriptAutosaveResult {
  const {
    enabled,
    dirty,
    value,
    storageKey,
    entityId = null,
    persist,
    onSynced,
    debounceMs = SCRIPT_AUTOSAVE_DEBOUNCE_MS,
    persistKeepalive,
  } = opts

  const [status, setStatusState] = useState<ScriptAutosaveStatus>('idle')
  const [hint, setHint] = useState('')
  const [versionDirty, setVersionDirty] = useState(false)
  const versionDirtyIdsRef = useRef<Set<string>>(new Set())
  const seqRef = useRef(0)
  const flushingRef = useRef(false)
  const valueRef = useRef(value)
  const dirtyRef = useRef(dirty)
  const enabledRef = useRef(enabled)
  const persistRef = useRef(persist)
  const onSyncedRef = useRef(onSynced)
  const storageKeyRef = useRef(storageKey)
  const entityIdRef = useRef(entityId)
  const keepaliveRef = useRef(persistKeepalive)

  valueRef.current = value
  dirtyRef.current = dirty
  enabledRef.current = enabled
  persistRef.current = persist
  onSyncedRef.current = onSynced
  storageKeyRef.current = storageKey
  entityIdRef.current = entityId
  keepaliveRef.current = persistKeepalive

  const setStatus = useCallback((s: ScriptAutosaveStatus, h = '') => {
    setStatusState(s)
    setHint(h)
  }, [])

  // 草稿 dirty 仅驱动静默持久化；versionDirty 供「保存版本」按钮，不因 autosave 成功而清除
  useEffect(() => {
    if (!dirty || entityId == null) {
      setVersionDirty(entityId != null && versionDirtyIdsRef.current.has(String(entityId)))
      return
    }
    const key = String(entityId)
    versionDirtyIdsRef.current.add(key)
    setVersionDirty(true)
  }, [dirty, entityId])

  const flush = useCallback(async (): Promise<boolean> => {
    if (!enabledRef.current || !dirtyRef.current) return true
    if (flushingRef.current) {
      const freed = await waitWhile(() => flushingRef.current, 2500)
      if (!freed) return false
      if (!enabledRef.current || !dirtyRef.current) return true
    }
    const script = valueRef.current
    const eid = entityIdRef.current
    const sk = storageKeyRef.current
    flushingRef.current = true
    try {
      writeScriptLocalDraft(sk, script)
      await persistRef.current(script, eid)
      // 同一实体在保存期间又改了内容：保留 dirty，交给下一轮防抖
      if (entityIdRef.current === eid && valueRef.current !== script) return true
      // 已切走实体或内容仍匹配：按「本次保存的 entityId」回传，避免误清新实体
      clearScriptLocalDraft(sk)
      onSyncedRef.current?.(script, eid)
      return true
    } catch {
      writeScriptLocalDraft(sk, script)
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
    const eid = entityIdRef.current
    if (eid != null) versionDirtyIdsRef.current.delete(String(eid))
    setVersionDirty(false)
    // 成功路径保持静默，不刷「已自动保存」类文案
    setStatus('idle')
  }, [setStatus])

  // 脏内容 → 防抖持久化；本地草稿与防抖对齐写入（避免每键 localStorage）
  useEffect(() => {
    if (!enabled || !dirty) return
    const seq = ++seqRef.current
    const scheduledEntity = entityId
    const scheduledValue = value
    setStatus('pending')
    const timer = window.setTimeout(() => {
      void (async () => {
        if (seqRef.current !== seq) return
        if (!enabledRef.current || !dirtyRef.current) return
        if (valueRef.current !== scheduledValue || entityIdRef.current !== scheduledEntity) return
        writeScriptLocalDraft(storageKey, valueRef.current)
        setStatus('saving')
        const ok = await flush()
        if (seqRef.current !== seq) return
        if (ok && !dirtyRef.current) {
          setStatus('saved', new Date().toLocaleTimeString())
        } else if (!ok) {
          setStatus('error', '将保留在本地，网络恢复后重试')
        } else if (dirtyRef.current) {
          setStatus('pending')
        }
      })()
    }, debounceMs)
    return () => window.clearTimeout(timer)
  }, [enabled, dirty, value, storageKey, entityId, debounceMs, flush, setStatus])

  // 隐藏/卸载前补写本地草稿（弥补防抖窗口内的未落盘）
  useEffect(() => {
    const persistLocal = () => {
      if (!enabledRef.current || !dirtyRef.current) return
      writeScriptLocalDraft(storageKeyRef.current, valueRef.current)
    }
    const onBeforeUnload = () => {
      persistLocal()
      flushKeepalive()
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') persistLocal()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      document.removeEventListener('visibilitychange', onVisibility)
      persistLocal()
    }
  }, [flushKeepalive])

  return { status, hint, versionDirty, setStatus, flush, flushKeepalive, markVersionSaved }
}
