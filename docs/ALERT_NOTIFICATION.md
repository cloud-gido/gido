# GIDO 告警中心与通知配置

GIDO Batch **告警中心**聚合工作流/节点失败与运维事件，支持多渠道通知。**不依赖** DolphinScheduler 内置 Alert 插件（DS 对终端用户隐藏）。

---

## 1. 功能概览

| 能力 | 说明 |
|------|------|
| 告警列表 | 按工作空间筛选；展示工作流、节点、业务日期、严重级别、日志摘要 |
| 状态管理 | 打开 / 已确认 / 已关闭 |
| 手动通知 | 对单条告警触发通知 |
| 自动通知 | 新告警创建时按规则推送（可配置最低严重级别） |
| 测试通知 | 管理员发送测试消息验证通道 |

入口：**GIDO Batch → 告警中心**（`/gido/batch/alerts`）。

---

## 2. 通知渠道

| 渠道 | 配置字段 | 说明 |
|------|----------|------|
| **邮件** | SMTP 主机/端口/用户/密码、发件人、收件人 | 支持 TLS |
| **通用 Webhook** | URL | JSON POST，适合自建集成 |
| **飞书 / Lark** | 群机器人 Webhook URL | 卡片式文本 |
| **企业微信** | 群机器人 Webhook URL | 文本消息 |

工作空间级配置存储在 `AlertNotificationConfig`；全局环境变量可作为默认值（如 `ALERT_WEBHOOK_URL`、`SMTP_*`）。

---

## 3. API（工作空间管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/alerts` | 分页列表（含 `node_name`、`business_date`、`log_summary` 等） |
| `GET` | `/alerts/notification/config` | 读取通知配置（敏感字段脱敏） |
| `PUT` | `/alerts/notification/config` | 更新通知配置（需 `admin` 权限） |
| `POST` | `/alerts/notification/test` | 发送测试通知 |
| `POST` | `/alerts/{id}/notify` | 对指定告警立即通知 |

配置写入与测试接口通过 `check_workspace_permission(..., "admin")` 限制为工作空间管理员。

---

## 4. 配置步骤

### 4.1 UI 配置（推荐）

1. 以工作空间**管理员**登录 GIDO Batch  
2. 打开 **告警中心** → **通知设置**（抽屉）  
3. 启用通知，选择最低严重级别（`info` / `warning` / `error` / `critical`）  
4. 按需开启邮件 / Webhook / 飞书 / 企微并填写参数  
5. 点击 **发送测试** 验证  
6. 保存配置  

### 4.2 环境变量默认值（可选）

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=***
SMTP_FROM=GIDO Alerts <alerts@example.com>
ALERT_WEBHOOK_URL=https://hooks.example.com/gido-alerts
```

工作空间 UI 配置会覆盖同名字段（SMTP 密码等仅存库内，API 返回脱敏值）。

---

## 5. 严重级别与过滤

自动通知仅推送 **大于等于** 配置的 `min_severity` 的告警：

`info` < `warning` < `error` < `critical`

生产环境建议默认 `error` 或 `critical`，避免通知风暴。

---

## 6. 与运维中心的关系

| 场景 | 行为 |
|------|------|
| 节点实例失败 | 同步任务写入 `AlertEvent`，可选自动通知 |
| 工作流实例失败 | 同上，列表展示工作流维度 |
| 手动重试成功 | 可关闭或确认相关告警 |

日志摘要来自调度引擎任务日志（经 `scheduler_ops` 拉取），非 DS UI。

---

## 7. 排障

| 现象 | 检查 |
|------|------|
| 无告警数据 | DS 是否启用、实例同步是否正常、Token 是否有效 |
| 测试通知失败 | SMTP 防火墙、Webhook URL、机器人是否被禁言 |
| 163/QQ 465 超时 | 465 须 **SSL**（GIDO 已自动处理）；密码须为**授权码**非登录密码 |
| 465 仍超时 | K8s 集群可能封禁出站 SMTP，改 587+TLS 或走 Webhook |
| 自动通知未触发 | `enabled` 是否为 true、`min_severity` 是否过高 |
| 401 导致无实例 | 见 [TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) §1 |

---

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| [SCHEDULER_INTEGRATION.md](./SCHEDULER_INTEGRATION.md) | 调度与实例同步架构 |
| [PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) | Batch 模块完整度 |
