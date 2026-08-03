---
name: gido-workspace-folder-tree
description: >-
  Keeps GIDO workspace folder/script side trees on one shared component across
  Batch Studio, Probe, and Stream Studio. Use when changing folder tree UI,
  drag-drop reorder/reparent, inline rename, locate/expand, or tree sort helpers.
---

# GIDO 工作区目录树：三端复用同步

## 硬性约定（用户要求，必须遵守）

就是这三端的功能要复用，改了啥都要同步。

数据开发 Studio、数据探查 Probe、实时 Stream Studio 的左侧**目录 / 脚本（或作业）节点列表**，必须同一套共享组件与工具函数；禁止只改其中一个页面留下行为分裂。

## 必须同步触达的入口

1. `gido/frontend/src/pages/Studio.tsx`
2. `gido/frontend/src/pages/Probe.tsx`
3. `gido/frontend/src/pages/StreamStudio.tsx`

三端只接线（API、权限、文案、id 类型），**不各自实现**树 UI / 拖拽 / 行内重命名。

## 共享实现（改行为时先改这里）

| 能力 | 位置 |
|------|------|
| 树 UI、拖拽、行内重命名、展开定位 | `gido/frontend/src/components/WorkspaceFolderTree.tsx` |
| 拖放意图 / 同级插入顺序 | `gido/frontend/src/utils/treeDropOrder.ts`（及同名测试） |
| 目录/叶子排序 | `gido/frontend/src/utils/treeSort.ts` |

覆盖范围包括但不限于：

- 同级排序、迁入子目录、移出到上一级/根
- 双击 / 菜单行内重命名（目录与叶子）
- `locateLeafInFolderTree` 展开祖先并滚动
- 只读态、根「新建目录」、菜单扩展 `folderMenuExtra`

## 页面层允许的差异

- **ID 类型**：Studio / Stream 多为 `number`；Probe 本地目录可为 `string`（组件已用 `TreeId`）
- **API**：`studioApi` / `streamingApi` / Probe 本地 store
- **叶子含义**：脚本节点 vs 流作业 vs Probe 查询
- **权限 / 锁**：如 Studio 锁定脚本不可重命名、Stream 运行中/锁定不可重命名——写在该页的 `onRenameLeaf` 回调里，不复制一整棵树
- **复制叶子**：Stream 可提供 `onCopyLeaf`；Studio/Probe 可不传

## 删除语义（三端对齐）

| | 有子目录 | 有叶子（脚本/作业/查询） |
|--|----------|--------------------------|
| Studio / Stream | 拒删 | 叶子 `folder_id` 置空（移到根）后删目录 |
| Probe | 拒删 | 查询 `folderId` 置空后删目录（与上对齐） |

叶子删除：三端均应二次确认（`Modal.confirm`）。

## 禁止

- 在任一页面内再写一套 `Tree` + 自研 drop/rename
- 只修 Studio 拖拽或重命名、不验证 Probe / Stream（或反之）
- 把 rc-tree / antd `info.node` 的落点语义当最终真相（同级重排须用真实悬停行，见 `treeDropOrder` 注释）
- Stream 新建作业写死 `folder_id: null`、却不提供目录菜单「新建作业」

## Agent 检查清单

- [ ] 行为改动是否落在 `WorkspaceFolderTree` / `treeDropOrder` / `treeSort`？
- [ ] Studio、Probe、StreamStudio 是否仍只接线共享组件？
- [ ] 行内重命名是否用 ref 读最新输入（避免 `useMemo` 树节点闭包旧名）？
- [ ] 目录与叶子同级排序是否都用悬停行 + 半行？
- [ ] Stream 是否支持目录内新建作业，且行内重命名与工具栏锁/运行中规则一致？
- [ ] 相关单测（`treeDropOrder.test.ts`）与后端集成测（`test_studio_folder_tree_integration.py` / `test_stream_folder_tree_integration.py`）是否更新？
