# 编辑器会话（产品说明）

## 范围

| 产品面 | 会话形态 |
|--------|----------|
| **数据开发（Batch Studio）** | 本机多 Tab：恢复上次打开的脚本标签；壳一次挂齐，正文按需加载 |
| **实时开发（Stream Studio）** | 仅恢复上次作业，不做多 Tab |
| **数据探查 / 工作流节点弹窗** | **不**叠编辑器 Tab 会话 |

对外叙述请明确：**多 Tab 会话恢复仅批开发**。

## 用户体感（对齐 IDEA / PyCharm）

1. 进入数据开发：上次的 Tab **标题同时出现**（不再逐个蹦出）
2. 当前 Tab 拉脚本；其它 Tab 为斜体弱化（未加载缓冲）
3. 点击后台 Tab 再拉取；失败可「重新加载」或再点 Tab 重试
4. 关闭 Tab = 移出会话，下次进入不再自动打开

## 技术要点

- 存储：`localStorage` `gido.editorSession.v1.studio.{workspaceId}`，只存 `tabIds` / `activeId`
- 共享：`editorSessionStore`、`studioTabChrome`、`StudioEditorTabStrip`
- 测试：单元 + Tab 条 UI E2E（Vitest）+ Playwright 浏览器冒烟（API mock）
