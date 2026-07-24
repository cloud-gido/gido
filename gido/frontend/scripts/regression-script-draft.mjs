/**
 * 前端草稿/断网语义回归（无需 vitest / 浏览器）。
 * 运行：node gido/frontend/scripts/regression-script-draft.mjs
 */
import assert from 'node:assert/strict'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)) },
  removeItem: (k) => { store.delete(k) },
}

const PREFIX = 'gido.scriptDraft.v1'
function scriptDraftStorageKey(scope, entityId) {
  return `${PREFIX}.${scope}.${entityId}`
}
function readScriptLocalDraft(storageKey) {
  if (!storageKey) return null
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed.script !== 'string') return null
    return parsed
  } catch {
    return null
  }
}
function writeScriptLocalDraft(storageKey, script) {
  if (!storageKey) return
  localStorage.setItem(storageKey, JSON.stringify({ script, updatedAt: Date.now() }))
}
function clearScriptLocalDraft(storageKey) {
  if (!storageKey) return
  localStorage.removeItem(storageKey)
}
function restoreScriptLocalDraft(storageKey, serverScript) {
  const draft = readScriptLocalDraft(storageKey)
  if (!draft) return null
  if (draft.script === (serverScript ?? '')) return null
  return draft.script
}

/** 模拟防抖：只在 schedule 到期时写本地（与 useScriptAutosave 对齐） */
function scheduleDraftWrite(storageKey, getScript, debounceMs, onFire) {
  let timer = null
  return {
    onChange() {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        const script = getScript()
        writeScriptLocalDraft(storageKey, script)
        onFire?.(script)
      }, debounceMs)
    },
    flushNow() {
      if (timer) clearTimeout(timer)
      timer = null
      const script = getScript()
      writeScriptLocalDraft(storageKey, script)
      return script
    },
    cancel() {
      if (timer) clearTimeout(timer)
      timer = null
    },
  }
}

async function sleep(ms) {
  await new Promise(r => setTimeout(r, ms))
}

// --- 实体隔离 ---
const keyA = scriptDraftStorageKey('studio.1', 10)
const keyB = scriptDraftStorageKey('stream.1', 20)
writeScriptLocalDraft(keyA, 'select a')
writeScriptLocalDraft(keyB, 'select b')
assert.equal(restoreScriptLocalDraft(keyA, 'server'), 'select a')
assert.equal(restoreScriptLocalDraft(keyB, 'server'), 'select b')
assert.equal(restoreScriptLocalDraft(keyA, 'select a'), null)
clearScriptLocalDraft(keyA)
assert.equal(restoreScriptLocalDraft(keyA, 'x'), null)
assert.equal(restoreScriptLocalDraft(keyB, 'x'), 'select b')

// --- 断网：防抖窗口内切实体前应 flushNow 落本地 ---
let script = 'select online'
const key = scriptDraftStorageKey('studio.9', 99)
const sch = scheduleDraftWrite(key, () => script, 50)
script = 'select typing-1'
sch.onChange()
script = 'select typing-2'
sch.onChange()
// 模拟切 Tab：先 flushNow（Studio/Stream 行为），再换实体
const flushed = sch.flushNow()
assert.equal(flushed, 'select typing-2')
assert.equal(restoreScriptLocalDraft(key, 'select online'), 'select typing-2')

// --- 断网未 flush：防抖未到期则本地仍无稿（性能取舍）---
store.clear()
script = 'select ephemeral'
const sch2 = scheduleDraftWrite(key, () => script, 200)
sch2.onChange()
assert.equal(restoreScriptLocalDraft(key, 'select online'), null, '防抖未到期不应落盘')
sch2.cancel()

// --- 防抖到期后落盘 ---
script = 'select debounced'
const sch3 = scheduleDraftWrite(key, () => script, 30)
sch3.onChange()
await sleep(60)
assert.equal(restoreScriptLocalDraft(key, 'select online'), 'select debounced')

// --- 恢复网络后：本地 ≠ 服务端 → 应回灌（由后端 PUT 完成，此处断言判定条件）---
const server = 'select online'
const local = restoreScriptLocalDraft(key, server)
assert.ok(local)
assert.notEqual(local, server)

console.log('regression-script-draft: OK (isolation + offline debounce + restore)')
