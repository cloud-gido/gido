---
name: gido-editor-autosave
description: >-
  Keeps GIDO script editors on one shared autosave/draft template across
  Batch Studio, Probe, Stream Studio, and Workflow DAG NodeConfigModal.
  Use when changing editor save/autosave/draft/version-history behavior,
  Monaco script panes, or NodeConfigModal script editing in GIDO frontend.
---

# GIDO 脚本编辑：统一自动保存模板

## 硬性约定（用户要求，必须遵守）

数据开发 Studio、数据探查 Probe、实时 Stream Studio、**工作流 DAG 双击节点后的作业编辑（`NodeConfigModal`）**，以及后续同类 Monaco/脚本编辑面，**必须同一套模板、同一套组件/Hook，同步修改**。

禁止只改其中一个页面留下行为分裂。

## 产品分层（草稿 ≠ 版本）

| 层 | 行为 | 版本历史 |
|----|------|----------|
| **静默草稿** | 编辑后防抖写入权威存储；切 Tab / 关页 / 释锁前 flush；**成功路径不对用户提示、不改工具栏布局** | **不写** |
| **显式保存版本** | 工具栏「保存版本」类按钮（`versionDirty` 星标，不因草稿 autosave 闪烁） | **写入**可回滚历史 |
| **本地兜底** | `localStorage` 防断网/失败丢稿；仅失败时 `AutosaveStatusHint` 提示 | 非权威 |

权威存储：

- Studio / DAG 节点脚本 → 服务端 `TaskNode.script_content`（`create_history=false` 草稿）
- Stream 作业 → 服务端 streaming job（`create_history=false` 草稿）
- Probe → 服务端 `GET/PUT /probe/tree`（按空间+用户）；本机 `probeLocalStore` 仅作缓存与离线回退

## 共享实现（改行为时先改这里）

- Hook：`gido/frontend/src/hooks/useScriptAutosave.ts`（防抖约 1.6s、`entityId` 防串清 dirty、`versionDirty`、flush、keepalive；本地草稿与防抖对齐，勿每键写 localStorage）
- 状态条：`gido/frontend/src/components/AutosaveStatusHint.tsx`（**仅 error 可见**；pending/saving/saved 禁止渲染）
- 本地草稿：`gido/frontend/src/utils/scriptLocalDraft.ts`
- API：`studioApi.saveDraft` / `streamingApi.saveDraft`（`create_history=false`）
- 关 Tab / 切 Tab：flush 失败须保留 dirty 与编辑锁，并提示；禁止失败后强行 `releaseEditLock`
- 工作台壳（布局，非草稿逻辑）：`gido/frontend/src/components/StudioWorkbenchShell.tsx` — Studio / Stream Studio / Probe 共用 bleed + 左树 + 顶栏/工具栏/舞台；改壳层样式须三处一起走该组件；**当前实体名**用 `StudioWorkbenchActiveEntityTitle`（plain 顶栏 / chip 工具栏），禁止页面内散写标题 Tag
- **脚本快捷键**：`gido/frontend/src/utils/monacoScriptKeybindings.ts`（`bindMonacoScriptKeybindings`：Cmd/Ctrl+/ 注释、Cmd/Ctrl+Enter 选中或全文试跑；选中可执行语句时行号旁 ▶ 见 `monacoSelectionRunGlyph.ts`）。四处 Monaco `onMount` 须在 `bindMonacoFindKeybindings` 之后同步接入；禁止只改一页。
- **编辑器会话恢复（本机）**：见 `docs/EDITOR_SESSION.md`。`editorSessionStore` + `studioTabChrome` + `StudioEditorTabStrip`（激活 Tab 用容器 `scrollLeft` 滚到完整可见，禁止 `Element.scrollIntoView` 以免祖先误滚/居中；右侧 ▾ 溢出列表展全名）。Studio 多 Tab 壳一次挂齐、正文懒加载；失败可重试；关闭移出会话。Stream 仅 activeId；Probe / NodeConfigModal 不叠。测试：Vitest 单元/流程/Tab UI E2E + Playwright `e2e/studio-session.spec.ts`（API mock）。**恢复完成前禁止 persist**。

## 必须同步触达的入口

1. `gido/frontend/src/pages/Studio.tsx`
2. `gido/frontend/src/pages/Probe.tsx`（`localAuthority` 文案）
3. `gido/frontend/src/pages/StreamStudio.tsx`
4. `gido/frontend/src/components/NodeConfigModal.tsx`

## 其它约定

- 协作锁 / 发布锁：仅持有编辑锁且未发布锁定时可写服务端草稿
- 后端静默草稿不得刷爆 `NodeHistory` / Stream 历史表
- 显式保存可用 `message.success('已保存并记入版本历史')`；禁止用「已自动保存 / 正在自动保存」占用工具栏
- 试跑覆盖脚本（`script_content` body）为请求级，**不得**静默写库；恢复会话时**不得**自动占编辑锁

## Agent 检查清单

- [ ] 是否影响多个编辑入口？若是 → 改共享层并四处接线
- [ ] 静默草稿是否避免写版本历史？
- [ ] 显式保存是否仍写历史？
- [ ] DAG `NodeConfigModal` 是否同步？
- [ ] 成功路径是否无状态文案跳动？失败是否仍可感知？
- [ ] 「保存版本 *」是否用 `versionDirty` 而非草稿 dirty？
- [ ] Cmd+/ / Cmd+Enter 是否走 `monacoScriptKeybindings` 且四端同步？
- [ ] Studio/Stream 会话恢复是否只经 `editorSessionStore`（不存正文）？
