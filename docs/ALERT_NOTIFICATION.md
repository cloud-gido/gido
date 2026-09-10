# GIDO 告警中心与通知配置

GIDO Batch **告警中心**聚合工作流失败与运维事件，支持多渠道通知。**不依赖** DolphinScheduler 内置 Alert 插件（DS 对终端用户隐藏）。

值班原则：**一次工作流失败只推一张飞书卡片**；节点失败只在告警中心展示，不刷屏。

---

## 1. 功能概览

| 能力 | 说明 |
|------|------|
| 告警列表 | 默认看未处理；展示工作流、失败节点、业务日期、日志摘要 |
| 状态管理 | 打开 / 已确认 / 已关闭 |
| 自动通知 | 工作流失败入库后立刻按渠道推送（飞书为交互卡片） |
| 增量推送 | 保存通知配置时默认把推送起点设为现在；更早结束的失败只进告警中心，不刷群 |
| 冷却 | 同一工作空间 + 同一工作流，默认 15 分钟内不重复推失败通知 |
| 静默 | 可设 N 小时不推送；恢复通知仍会推（`force`） |
| 恢复 | 同一实例从失败变为成功时关闭未处理失败告警，并推一条绿色恢复卡片 |
| 手动通知 | 对单条告警强制再推 |
| 测试通知 | 管理员发送测试消息验证通道 |
| 深链 | 平台集成「站点入口」或环境变量 `GIDO_PUBLIC_URL` 后，飞书卡片可打开实例中心 |

入口：**GIDO Batch → 告警中心**（`/gido/batch/alerts`）。

飞书必须配置在**发布该工作流的工作空间**（不是随便一个 `infras` 空间）。从未经过 GIDO 发布的 Dolphin 作业不会出现在本页。

---

## 2. 通知渠道

| 渠道 | 配置字段 | 说明 |
|------|----------|------|
| **邮件** | SMTP 主机/端口/用户/密码、发件人、收件人 | 支持 TLS |
| **通用 Webhook** | URL | JSON POST，适合自建集成 |
| **飞书 / Lark** | 群机器人 Webhook URL | 交互卡片；失败红、恢复绿 |
| **企业微信** | 群机器人 Webhook URL | 文本消息 |

工作空间级配置存储在 `AlertNotificationConfig`；全局环境变量可作为默认值（如 `ALERT_WEBHOOK_URL`、`SMTP_*`）。

浏览器回跳基址（优先级：平台集成「站点入口」> 环境变量 `GIDO_PUBLIC_URL`）：

```bash
GIDO_PUBLIC_URL=https://gido.example.com
```

推荐在 **系统管理 → 平台集成 → 站点入口** 填写并保存；留空即拔掉库覆盖，回退环境变量。未配置时卡片没有「打开实例中心」按钮。链接形状：`{入口}/gido/batch/operation?workspace_id=&instance=`。

这是全站配置，不要按工作空间各写一份（同一套 GIDO 只有一个浏览器地址）。

---

## 3. API（工作空间管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/alerts` | 分页列表。平台管理员可加 `include_all_workspaces=true` |
| `GET` | `/alerts/notification/config` | 读取通知配置（敏感字段脱敏；含 `notify_cooldown_minutes`、`muted_until`） |
| `PUT` | `/alerts/notification/config` | 更新通知配置（需 `admin`）。`mute_hours` 为写入项：`>0` 静默，`0` 解除 |
| `POST` | `/alerts/notification/test` | 发送测试通知 |
| `POST` | `/alerts/{id}/notify` | 对指定告警立即通知 |

配置写入与测试接口通过 `check_workspace_permission(..., "admin")` 限制为工作空间管理员。

---

## 4. 配置步骤

### 4.1 UI 配置（推荐）

1. 以工作空间**管理员**登录 GIDO Batch  
2. 打开 **告警中心** → **通知配置**  
3. 打开飞书/邮件等渠道（打开渠道会自动打开总开关）  
4. 按需调整最低严重级别、冷却分钟、静默小时  
5. 点击 **测试发送** 验证  
6. 保存配置  

### 4.2 环境变量默认值（可选）

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=***
SMTP_FROM=GIDO Alerts <alerts@example.com>
ALERT_WEBHOOK_URL=https://hooks.example.com/gido-alerts
GIDO_PUBLIC_URL=https://gido.example.com
```

工作空间 UI 配置会覆盖同名字段（SMTP 密码等仅存库内，API 返回脱敏值）。

---

## 5. 严重级别与过滤

自动通知在 **工作流失败告警创建时** 立即推送。只要工作空间打开了飞书/邮件等渠道就会发；总开关未开但渠道已开时同样会推（避免只开了 Lark 却忘了「启用通知」）。

`info` < `warning` < `error` < `critical`

生产环境建议默认 `error`。恢复通知为 `info`，但走 `force_notify`，不受最低级别和冷却限制。

---

## 6. 与实例中心的关系

| 场景 | 行为 |
|------|------|
| 打开实例中心 | 同步**当前工作空间**最近 DS 实例（含已失败），再刷新仍在运行的 |
| 「同步调度实例」 | 空间开发者可同步本空间；不传 `workspace_id` 时仅平台管理员可全量同步 |
| 工作流实例失败 | 写一条工作流级 `AlertEvent` 并推飞书卡片（含失败节点名） |
| 节点实例失败 | 写入告警中心，**不**单独推飞书 |
| 同一实例恢复成功 | 关闭未处理失败告警，推恢复卡片 |
| 平台管理员 | 实例中心 / 告警中心可开「全部工作空间」 |

日志摘要来自调度引擎任务日志（经 `scheduler_ops` 拉取），非 DS UI。轮询补偿约 **15 秒**；DS 回调带 `processDefinitionCode` 时也会即时入库。

---

## 7. 排障

| 现象 | 检查 |
|------|------|
| 无告警数据 | DS 是否启用、实例是否已同步进 **发布该工作流的空间**、Token 是否有效 |
| 测试通知失败 | SMTP 防火墙、Webhook URL、机器人是否被禁言 |
| 163/QQ 465 超时 | 465 须 **SSL**（GIDO 已自动处理）；密码须为**授权码**非登录密码 |
| 465 仍超时 | K8s 集群可能封禁出站 SMTP，改 587+TLS 或走 Webhook |
| 自动通知未触发 | 是否打开了飞书等渠道、是否在冷却/静默、`min_severity` 是否过高。定时实例须先同步进 GIDO（轮询约 15s，或 DS 回调） |
| 实例中心没有失败 | 换到发布该工作流的工作空间；打开实例中心或点「同步调度实例」；确认工作流已由 GIDO 发布 |
| 飞书卡片没有按钮 | 平台管理员到「平台集成 → 站点入口」填写浏览器地址，或设环境变量 `GIDO_PUBLIC_URL` |
| 401 导致无实例 | 见 [TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) §1 |

---

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| [SCHEDULER_INTEGRATION.md](./SCHEDULER_INTEGRATION.md) | 调度与实例同步架构 |
| [PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) | Batch 模块完整度 |
