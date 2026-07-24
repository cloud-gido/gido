/**
 * 轻量回归：scriptLocalDraft 读写/恢复语义（无需 vitest）。
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

const keyA = scriptDraftStorageKey('stream.1', 10)
const keyB = scriptDraftStorageKey('stream.1', 20)

writeScriptLocalDraft(keyA, 'select a')
assert.equal(restoreScriptLocalDraft(keyA, 'select server'), 'select a')
assert.equal(restoreScriptLocalDraft(keyA, 'select a'), null, '与服务端一致时不恢复')

writeScriptLocalDraft(keyB, 'select b')
clearScriptLocalDraft(keyA)
assert.equal(restoreScriptLocalDraft(keyA, 'x'), null)
assert.equal(restoreScriptLocalDraft(keyB, 'x'), 'select b', '不同实体草稿隔离')

// 模拟：切作业不应读到上一作业草稿
assert.notEqual(keyA, keyB)
assert.equal(restoreScriptLocalDraft(keyA, 'select b'), null)

console.log('regression-script-draft: OK')
