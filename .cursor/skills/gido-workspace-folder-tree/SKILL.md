---
name: gido-workspace-folder-tree
description: >-
  Keeps GIDO workspace folder/script side trees on one shared component across
  Batch Studio, Probe, and Stream Studio. Use when changing folder tree UI,
  drag-drop move/reparent, inline rename, locate/expand, or tree sort helpers.
---

# GIDO 工作区目录树：三端复用同步

## 硬性约定（用户要求，必须遵守）

就是这三端的功能要复用，改了啥都要同步。

数据开发 Studio、数据探查 Probe、实时 Stream Studio 的左侧**目录 / 脚本（或作业）节点列表**，必须同一套共享组件与工具函数；禁止只改其中一个页面留下行为分裂。

## 排序与拖拽（对齐操作系统目录树）

同级固定规则（不可手工调序）：

1. **目录在前，脚本/作业在后**
2. **组内按名称字典序**（`zh-CN` + `numeric`）
3. 拖拽**只做换位置**：迁入其它目录、移到上一级/根；同级拖放提示不支持调序

参考：macOS Finder / Windows 资源管理器「按名称」视图、IDEA Project 视图。

## 必须同步触达的入口

1. `gido/frontend/src/pages/Studio.tsx`
2. `gido/frontend/src/pages/Probe.tsx`
3. `gido/frontend/src/pages/StreamStudio.tsx`

三端只接线（API、权限、文案、id 类型），**不各自实现**树 UI / 拖拽 / 行内重命名。

## 共享实现（改行为时先改这里）

| 能力 | 位置 |
|------|------|
| 树 UI、拖拽迁入/移出、行内重命名、展开定位 | `gido/frontend/src/components/WorkspaceFolderTree.tsx` |
| 拖放迁移动机 | `gido/frontend/src/utils/treeDropOrder.ts` |
| 名称字典序 | `gido/frontend/src/utils/treeSort.ts` |

## 页面层允许的差异

- **ID 类型**：Studio / Stream 多为 `number`；Probe 本地目录可为 `string`
- **API**：`studioApi` / `streamingApi` / Probe 本地 store
- **叶子含义**：脚本节点 vs 流作业 vs Probe 查询
- **权限 / 锁**：写在页面 `onRenameLeaf` 等回调
- **复制叶子**：Stream 可提供 `onCopyLeaf`

## 删除语义（三端对齐）

| | 有子目录 | 有叶子 |
|--|----------|--------|
| Studio / Stream / Probe | 拒删 | 叶子移到根后删目录 |

叶子删除：三端均应二次确认。

## 禁止

- 在任一页面内再写一套 `Tree`
- 恢复同级手工排序 / 半行插队启发式
- 只改三端之一

## Agent 检查清单

- [ ] 同级是否仍「目录→脚本 + 字典序」、无手工排序？
- [ ] 拖拽是否只换父级 / 迁入？
- [ ] Studio、Probe、StreamStudio 是否只接线共享组件？
- [ ] `treeDropOrder` / `treeSort` 单测是否更新？
