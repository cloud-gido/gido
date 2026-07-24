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

## 产品分层（对齐 DataWorks）

| 层 | 行为 | 版本历史 |
|----|------|----------|
| **静默草稿** | 编辑后防抖写入权威存储；切 Tab / 关页 / 释锁前 flush | **不写** |
| **显式保存版本** | 工具栏「保存版本」类按钮 | **写入**可回滚历史 |
| **本地兜底** | `localStorage` 防断网/失败丢稿 | 非权威 |

权威存储：

- Studio / DAG 节点脚本 → 服务端 `TaskNode.script_content`（`create_history=false` 草稿）
- Stream 作业 → 服务端 streaming job（`create_history=false` 草稿）
- Probe → 本机 `probeLocalStore`（local-first），UX 仍走共用 Hook

## 共享实现（改行为时先改这里）

- Hook：`gido/frontend/src/hooks/useScriptAutosave.ts`（防抖约 1.6s、status、flush、keepalive、local draft）
- 状态条：`gido/frontend/src/components/AutosaveStatusHint.tsx`
- 本地草稿：`gido/frontend/src/utils/scriptLocalDraft.ts`
- API：`studioApi.saveDraft` / `streamingApi.saveDraft`（`create_history=false`）

## 必须同步触达的入口

1. `gido/frontend/src/pages/Studio.tsx`
2. `gido/frontend/src/pages/Probe.tsx`（`localAuthority` 文案）
3. `gido/frontend/src/pages/StreamStudio.tsx`
4. `gido/frontend/src/components/NodeConfigModal.tsx`

## 其它约定

- 协作锁 / 发布锁：仅持有编辑锁且未发布锁定时可写服务端草稿
- 后端静默草稿不得刷爆 `NodeHistory` / Stream 历史表
- UI 文案区分「已自动保存」与「已保存并记入版本历史」

## Agent 检查清单

- [ ] 是否影响多个编辑入口？若是 → 改共享层并四处接线
- [ ] 静默草稿是否避免写版本历史？
- [ ] 显式保存是否仍写历史？
- [ ] DAG `NodeConfigModal` 是否同步？
- [ ] Probe / Stream 体验是否与 Studio 一致（状态提示、防丢稿）？
