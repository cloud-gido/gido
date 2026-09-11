# GIDO 告警中心与通知配置

GIDO Batch **告警中心**聚合工作流失败与运维事件，支持多渠道通知。**不依赖** DolphinScheduler 内置 Alert 插件（DS 对终端用户隐藏）。

值班原则：**一次工作流失败只推一张飞书卡片**；节点失败只在告警中心展示，不刷屏。

---

## 1. 功能概览

| 能力 | 说明 |
|------|------|
| 告警列表 | 默认看未处理、且隐藏推送起点之前的历史入库；可按工作流名、通知状态检索 |
| 状态管理 | 打开 / 已确认 / 已关闭 |
| 自动通知 | 工作流失败入库后立刻按渠道推送（飞书为交互卡片） |
| 增量推送 | 保存通知配置时默认把推送起点设为现在；更早结束的失败只进告警中心，不刷群 |
| 冷却 | 同一工作空间 + 同一工作流，默认 15 分钟内不重复推失败通知 |
| 静默 | 可设 N 小时不推送；恢复通知仍会推（`force`） |
| 恢复 | 同一实例从失败变为成功时关闭未处理失败告警，并推一条绿色恢复卡片 |
| 手动通知 | 对单条告警强制再推 |
| 测试通知 | 管理员发送测试消息验证通道；卡片可打开告警中心 |
| 覆盖检查 | 无已发布工作流、飞书未开、未配站点入口时，告警中心与通知配置会提示 |
| 深链 | 平台集成「站点入口」或环境变量 `GIDO_PUBLIC_URL` 后，飞书卡片可打开实例中心或告警中心 |

入口：**GIDO Batch → 告警中心**（`/gido/batch/alert?workspace_id=`）。

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
| `GET` | `/alerts` | 分页列表。`q` 工作流名；`notification_status`；`after_armed` 默认 `true` 隐藏历史入库。返回 `coverage`。平台管理员可加 `include_all_workspaces=true` |
| `GET` | `/alerts/notification/config` | 读取通知配置（敏感字段脱敏；含 `notify_cooldown_minutes`、`muted_until`、`coverage`） |
| `PUT` | `/alerts/notification/config` | 更新通知配置（需 `admin`）。`mute_hours` 为写入项：`>0` 静默，`0` 解除 |
| `POST` | `/alerts/notification/test` | 发送测试通知；响应含 `coverage` |
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
| 打开实例中心 | 直接读 GIDO 实例事实表；停留在页面时约 15 秒刷新列表 |
| 打开告警中心 | 直接读告警表；停留时约 15 秒刷新 |
| 「立即采集」 | 后台采集之外的手动兜底，仅排障用。空间开发者可采集本空间；不传 `workspace_id` 时仅平台管理员可全量采集 |
| 轮询接口只读 | `/operation/overview`、`/operation/instances`、`/alerts` **不**在请求里调执行引擎，见 §6.8 |
| 工作流实例失败 | 写一条工作流级 `AlertEvent` 并推飞书卡片（含失败节点名） |
| 概览排行 | 近 7 日出错排行与耗时排行；点失败次数下钻到该工作流的失败实例 |
| 运行类型标签页 | 周期 / 补数据 / 重跑 / 手动分开看，见 §6.2 |
| 人工置成功 | 关闭该实例告警并锁定状态，采集不再覆盖，见 §6.3 |
| 实例运行图 | 这次运行当时的 DAG + 每个节点状态；看节点级情况的唯一入口，见 §6.4 |
| 运行诊断 | 「为什么这次没跑」，实例中心与告警中心都可打开，见 §6.7 |
| 值班指派 | 新告警落到当班人名下并在卡片里点名，见 §6.6 |
| 节点实例失败 | 写入告警中心，**不**单独推飞书 |
| 同一实例恢复成功 | 关闭未处理失败告警，推恢复卡片 |
| 平台管理员 | 实例中心 / 告警中心可开「全部工作空间」 |

运行数据由后台采集任务（`app/services/run_collector.py`）每 **15 秒**采一轮，不依赖用户打开页面：先拉执行引擎项目里最近 FAILURE（首页+末页），再按流程定义补近几天实例。生产一出现失败，一轮采集内应入库为告警 `pending`；真正推飞书由出站箱投递任务负责（约 5 秒一轮），**不在采集持锁路径里打 Webhook**。可选 HTTP 回调 `POST /api/scheduler/callback/dolphin`（`X-Internal-Token`）可在结束当秒入库。

告警闭环与引擎无关：执行引擎采集、引擎回调、以及 GIDO 内置 APScheduler 本地执行三条路径，失败都走 `open_instance_alert`、恢复都走 `resolve_instance_alerts_on_recovery`。回调接口在 `INTERNAL_TOKEN` 未配置时返回 503（拒绝而非放行）。

**运行台账**：一次引擎运行对应一行 `WorkflowInstance`，`scheduler_run_key` 上有唯一索引 `uq_workflow_instance_run_key`。重跑不再覆盖原实例，而是新开一行并用 `parent_instance_id` 指回被重跑的那次运行，历史运行号与结果都留得住；采集与回调并发写同一次运行时，后写的一方让位给已存在的行。

**增量采集**：每个流程定义在 `dw_scheduler_sync_cursors` 里维护一条水位线（已采集到的最大引擎实例号）。采集时先判断引擎列表的排序方向，再从最新一侧往回翻页，直到整页实例号都不高于水位线为止（单轮最多 25 页）。中间页不会像固定时间窗口那样被永久跳过。水位线只进不退，且该定义这一轮有任何一行写库失败就不推进，下一轮重扫。

**通知出站箱（outbox）**：`open_instance_alert(notify=True)` 只把事件标成 `pending` 并写入 `notify_next_retry_at`，**不**同步调用 Webhook/SMTP。后台 `dispatch_pending_notifications` 约每 5 秒扫一次：先投首次 pending，再投失败/静默到期的渠道。失败渠道记进 `notify_pending_channels`，退避 1/5/15/60 分钟，四次仍失败就停手并留在告警中心的「投递失败」里。只重投失败的渠道，已经送达的群不会被重复刷。人工点「重新通知」仍走同步投递。

---

## 6.1 基线：未按时完成与运行超时

失败告警只覆盖「跑了并且红了」。生产上更隐蔽的两类事故由基线负责：

| 类型 | `alert_type` | 判定 | 级别 |
|------|--------------|------|------|
| 未按时完成 | `sla` | 业务日期 D 到了承诺时间（D + `expect_finish_offset_days` 的 `expect_finish_time`，空间时区）仍没有 `success` 实例 | 规则配置，默认 `error` |
| 运行超时 | `timeout` | 实例仍在 `running`，且已运行超过 `max_duration_minutes` | `warning` |

关键点：

- **没有实例也会告警**。任务压根没被调度起来时没有实例可挂，此时写一条工作流级告警（`workflow_instance_id` 为空），去重键带业务日期，一个日期只报一次。
- **晚到也算到了**。补跑成功后基线告警自动 `resolved`，不需要人工点。超时告警在实例最终成功时一并关闭，但不推「已恢复」卡片——跑得慢但成功了不值得再刷一次群。
- **首次启用不刷历史**。承诺时间过去超过 30 小时的业务日期不再补告警。
- **冷却策略**：失败与超时按工作流做 15 分钟冷却；基线破线每个业务日期本就只有一条，不冷却。
- 配置入口：告警中心 →「基线」；接口 `GET/PUT/DELETE /api/alerts/sla/rules`，巡检任务 `sla_check` 每 60 秒一轮（`app/services/sla_monitor.py`）。

采集健康度随 `GET /api/operation/overview` 与 `GET /api/alerts` 一起返回（字段 `collector`：`enabled` / `lag_seconds` / `stale` / `last_error`），实例中心与告警中心据此显示「运行数据实时采集中」或落后告警。执行引擎（当前 DolphinScheduler，后续可换 Airflow 等）只在平台集成配置页出现，产品功能层不暴露引擎概念。

---

## 6.2 运行类型分离

周期运维、补数据、重跑、开发试跑混在一张表里看不清，实例中心按运行类型分标签页：

| 标签页 | `run_type` | 判定依据 |
|--------|-----------|---------|
| 周期实例 | `schedule` | 引擎 commandType 含 `SCHEDULER`/`TIMER`；或 `trigger_type` 为 `schedule` |
| 补数据实例 | `backfill` | commandType 含 `COMPLEMENT`；或 `trigger_type` 为 `backfill`/`batch` |
| 重跑实例 | `rerun` | commandType 含 `REPEAT_RUNNING`/`RECOVER`；或 `trigger_type` 为 `rerun` |
| 手动实例 | `manual` | commandType 含 `START_PROCESS`/`EXECUTE`；或 `trigger_type` 为 `manual`/`local`；**以及所有判不出来的** |

判定逻辑在 `app/services/workflow_trigger_display.py` 的 `classify_run_type`：引擎回填的 commandType 比 `trigger_type` 可信，优先看它；commandType 认不出来才退回 `trigger_type` 前缀。判不出来的一律归到手动实例，宁可污染手动视图，也不让实例从所有标签页里消失。

`GET /api/operation/instances?run_type=…` 做同样判定的 SQL 版本（`_run_type_condition`），所以分页和计数与展示一致；响应里的 `run_type_counts` 是「除运行类型外套用同一批过滤条件」算出来的，切标签页时数字不会跳。

---

## 6.3 人工置成功 / 置失败

数据已经被旁路修复时，不该让下游一直等这次运行真的跑通。实例中心对 `failed`/`killed` 实例提供「置成功」：

- 只改 GIDO 的运行台账，**不回写调度引擎**，也不会让引擎重跑。
- 置过之后 `status_override` 落库，采集、引擎回调、以及丢失实例标记三条路径都不再改这条实例的状态，也不再按引擎状态开/关它的告警——否则下一轮采集就会把刚关掉的告警重新推出来。
- 置成功会顺带关闭这条实例上挂着的 `failed`/`timeout`/`sla` 告警。
- 运行中的实例不允许置状态，要先终止。
- 「撤销人工状态」清掉 override，后续重新以引擎回报为准。

接口：`POST /api/workflows/{id}/instances/{inst_id}/override-status`（`status` 为 `success`/`failed`，可带 `reason`）、`DELETE` 同路径撤销。需要 developer 及以上角色。实例列表返回 `status_override` / `override_reason` / `override_at`，界面上显示为状态旁的「人工」标签。

注意粒度：GIDO 的跨工作流依赖判定看的是**工作流实例**是否成功（`workflow_dependent._item_success_local`），不看单个节点。所以放行下游只在实例级有意义，节点级不提供「置成功」。

---

## 6.4 实例运行图

实例中心每行有「运行图」，只读地展示这次运行的 DAG 与每个节点的状态。点节点看详情、日志，失败节点可直接重试，运行中的可终止。

这是看节点级情况的**唯一**入口。原先还有一份「节点明细」表格视图（`GET /operation/node-instances`），
和运行图完全重叠，两个入口只会让人犹豫点哪个，已经连同接口一起移除；告警里的「打开实例运行图」
带上 `workflow` 与 `instance` 两个参数直接落到运行图。表格上下文里的「最近同步 / 采集报错」
已并入运行图的 `instance` 字段——图上状态可不可信全看这两个。

关键点是**图取实例所属版本的快照**（`JobVersion.dag_snapshot`），不是当前编排。发布后改了图或改了名，回看历史实例才不会串：

- 节点名用发布时的名字，和引擎日志里的任务名对得上；GIDO 里的现用名单独作为 `current_name` 返回，不一致时在详情里提示。
- 快照里有、但这次没走到的节点状态是 `not_run`（「未运行」），不伪装成 `pending`。
- 老实例没绑版本时退回当前定义，`version_no` 返回 `null`，界面上相应改写提示文案。

接口：`GET /api/operation/workflows/{wf_id}/instances/{inst_id}/dag`。

---

## 6.5 节点实例与引擎任务的对齐

节点实例回填靠**发布时写下的 task code**（`JobVersion.dag_snapshot[].ds_task_code`）对回 GIDO 节点，名字只作为兜底：

- 之前只按名字匹配。发布后在 GIDO 里改名，引擎里还是旧名字，整片节点明细就丢了。
- 解析器按**实例所属版本**构建，不在工作流层面复用——同一工作流的不同实例可能跑的是不同版本。
- 名字撞车时以 code 为准，不会把状态写到另一个节点上。
- 老版本快照里没有 code 时按名字兜底，并把引擎返回的 code 顺带回填，下一轮就能走 code 匹配。
- 节点重试找 task code 也用版本快照（`scheduler_ops`）：重新发布会换掉 task code，按当前定义找会重试到别的任务。

实现见 `dolphin_instance_sync._NodeResolver` / `_node_resolver` / `_instance_dag_nodes`。

---

## 6.6 值班表与静默时段

两件事解决的是同一个问题：告警要找对人，在对的时间找。

**值班表**（告警中心 →「值班表」）按星期与时段排班：

- 新告警自动落到当班人名下（`AlertEvent.assignee_id`），飞书卡片底部点名「当前值班」，群里不用再问「这个谁看」。
- 时段支持跨零点（如 `22:00 – 06:00`），按工作空间时区判断。跨零点的班**归属于开班那一天**：周五 22:00 的夜班覆盖到周六凌晨，但不会让周五凌晨也算在班。
- 没排班时不指派，告警留给人自己认领。
- 接口：`GET/POST/PUT/DELETE /api/alerts/oncall/shifts`。列表里的 `on_call_now` 标出此刻在班的班次，排错时一眼看出时段配得对不对。

**静默时段**（通知配置里的「静默时段」）只压推送，不压记录：

- 时段内，低于 `quiet_hours_min_severity` 的告警**不推群**，但照常进告警中心，`notification_status` 记为 `deferred`，`notify_next_retry_at` 设为时段结束那一刻。
- 时段一结束，由已有的通知出站箱任务（约每 5 秒一轮）自动补推，**不会丢**。界面上显示为「静默时段内暂缓」并在 tooltip 里给出补推时间。
- 够严重的（默认仅 `critical`）在时段内照常立刻推。
- 起止必须成对且合法，否则接口返回 400——半配一半会静默得莫名其妙。

实现见 `app/services/alert_oncall.py`。

---

## 6.7 运行诊断

「这次为什么没跑 / 还没跑完」——实例中心每行和告警中心每条都有「诊断」按钮。只读 GIDO 自己的事实（工作流状态、运行台账、节点实例、跨工作流依赖、采集健康度），**不向执行引擎发请求**，所以基线告警可以直接把结论带进飞书卡片。

结论分 `blocker` / `warning` / `info` / `ok` 四级，按严重度排序，第一条作为 `verdict`：

| 结论 | 级别 | 含义 |
|------|------|------|
| `schedule_paused` | blocker | 周期调度已暂停，定义还在但不会自动触发 |
| `workflow_offline` / `not_published` | blocker | 没有生效的生产定义 |
| `no_cron_schedule` / `cron_missing` | blocker | 没配周期调度或 Cron 为空 |
| `no_instance` | blocker | 这个业务日期查不到实例 |
| `dependency_unmet` | blocker | 跨工作流依赖未满足，点名是哪个上游 |
| `instance_failed` | blocker | 跑了但失败了，点名失败节点 |
| `instance_long_running` | warning | 已运行超过 6 小时 |
| `collector_stale` / `collector_disabled` | warning | 采集落后或调度未启用 |
| `instance_success` / `no_blocker_found` | ok | 没发现平台侧阻塞 |

几个刻意的取舍：

- **已经成功了就不报「在等上游」**。依赖检查只在「没跑」或「还没跑完」时才做，否则只会把人带偏。
- **同一业务日期先失败后重跑成功，结论是成功**，不是失败。
- **采集落后要单独说**。此时「没有实例」可能只是还没同步到，不能直接判定没跑。
- **没有实例时给出下次调度时间**（按 Cron 预览），便于判断要不要手工补。
- 业务日期留空时取最近一次运行的业务日期；基线告警没有实例可挂，业务日期从去重键 `sla:workflow:{id}:biz:{date}` 里取，才能定到正确的那天。

接口：`GET /api/operation/diagnose?workspace_id=&workflow_id=&business_date=`。实现见 `app/services/run_diagnosis.py`。

---

## 6.8 轮询接口必须只读

实例中心与告警中心每 15 秒轮询各自的列表。以下三个接口**不得**在请求里同步调用执行引擎：

- `GET /operation/overview`
- `GET /operation/instances`
- `GET /alerts`

原先它们都会「兜底采集一轮」，而冷却时间**只在采集成功时才记**。引擎一慢就会连锁成事故：

1. 每一轮轮询都重新去打那个慢的引擎（冷却从未生效）；
2. 请求各自占着一个 DB 会话挂到网关超时，前端报 **524**；
3. 连接池被吃干之后，连纯读的告警中心也打不开——转圈但永远出不来。

采集只由后台 `scheduler_instance_poll` 负责（15s 一轮，`max_instances=1` + `coalesce`，不会堆积）。
采集是否落后由接口返回的 `collector` 字段告诉前端，页面顶部有状态条；平台管理员还有「立即采集」手动兜底。

回归由 `tests/test_polled_endpoints_never_touch_engine.py` 守住：任何一次引擎调用都会让测试失败。

前端两个页面的轮询失败统一走 `utils/pollError.ts`——挂一条横幅并说明会自动重试，
既不弹 toast 把屏幕刷满，也不留下没人接的 promise 让控制台刷满 `Uncaught AxiosError`。

---

## 7. 排障

| 现象 | 检查 |
|------|------|
| 无告警数据 | DS 是否启用、实例是否已同步进 **发布该工作流的空间**、Token 是否有效 |
| 测试通知失败 | SMTP 防火墙、Webhook URL、机器人是否被禁言 |
| 通知显示 failed ×N | 鼠标悬停「通知」列看待重投渠道与下次重投时间；显示「已达重试上限」说明退避四次都失败，需先修渠道再手动补发 |
| 163/QQ 465 超时 | 465 须 **SSL**（GIDO 已自动处理）；密码须为**授权码**非登录密码 |
| 465 仍超时 | K8s 集群可能封禁出站 SMTP，改 587+TLS 或走 Webhook |
| 自动通知未触发 | 是否打开了飞书等渠道、是否在冷却/静默、`min_severity` 是否过高。定时实例须先被采集进 GIDO（约 15s 一轮，或引擎回调） |
| 实例中心没有失败 | 先看页面顶部采集状态是否落后；再换到发布该工作流的工作空间；确认工作流已由 GIDO 发布并绑定生效 |
| 飞书卡片没有按钮 | 平台管理员到「平台集成 → 站点入口」填写浏览器地址，或设环境变量 `GIDO_PUBLIC_URL` |
| 401 导致无实例 | 见 [TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) §1 |

---

## 8. 相关文档

| 文档 | 说明 |
|------|------|
| [SCHEDULER_INTEGRATION.md](./SCHEDULER_INTEGRATION.md) | 调度与实例同步架构 |
| [PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) | Batch 模块完整度 |
