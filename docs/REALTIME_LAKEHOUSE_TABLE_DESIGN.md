# 实时湖仓表设计规范（Flink SQL + Paimon）

| 项 | 内容 |
|----|------|
| 文档状态 | 正式版（团队规范） |
| 适用对象 | 大数据 / 实时数仓 / 平台研发 |
| 适用场景 | 体育博彩 SaaS、多商户、Kafka + Flink SQL、Paimon（可选）、Doris 查询分析 |
| 相关文档 | [CDC → Paimon（EKS）](./CDC_PAIMON_EKS.md)、[Flink 架构](./FLINK_ARCHITECTURE.md)、[流式有状态作业 E2E](./STREAM_STATEFUL_OPERATIONS_E2E.md) |

---

## 1. 文档目的

本规范约定实时链路上 **表分层、命名、主键、Bucket、分区、时间、CDC 与写入参数** 的统一标准，目标是：

1. 稳定支撑 **CDC 实时同步**（订单、用户、赛事、盘口等）。
2. 支撑 **订单 / 风控等高频写入** 与多商户查询裁剪。
3. 支撑未来 **ODS → DWD → DWS → ADS** 数仓演进。
4. 降低 **小文件、Compaction、Bucket 设计** 等常见生产事故。
5. 下游接入 **Doris** 时，分层与字段语义可复用，避免推倒重来。

> **说明：** 本文以 Flink SQL + Apache Paimon 为默认落湖实现；其中主键、分区、时间、CDC、多租户裁剪等约定，同样适用于 `Kafka → Flink → Doris` 等路径。因此文档定名为「实时湖仓表设计规范」，而非仅「Paimon 规范」。

---

## 2. 设计原则

| # | 原则 | 说明 |
|---|------|------|
| 1 | 分层清晰 | ODS 保真、DWD 可分析、DWS 可汇总、ADS 服务出口 |
| 2 | 主键稳定 | 只选业务不变的唯一键；多租户表主键通常包含 `merchant_id` |
| 3 | 时间统一 | 湖内存储 **UTC**；展示由 BI / API / 前端按商户时区转换 |
| 4 | 写入可预期 | Bucket 数与 Sink 并行度匹配；目标文件大小显式配置 |
| 5 | 规范与引擎解耦 | 更换湖格式或直写 Doris 时，语义约定仍可复用 |

---

## 3. 分层规范

```text
ODS（原始明细 / CDC 最新状态）
 └─ DWD（清洗明细 / 统一维度与口径）
     └─ DWS（汇总宽表 / 主题指标）
         └─ ADS（应用层 / API · BI · 报表）
```

| 层 | 表前缀 | 职责 | 典型加工 |
|----|--------|------|----------|
| ODS | `ods_` | 业务库 / CDC 同步的原始或最新状态 | 少加工、保真、可回放 |
| DWD | `dwd_` | 清洗、标准化、关联后的明细 | 字典映射、时区归一、维度关联 |
| DWS | `dws_` | 按天 / 主题汇总 | 聚合指标、轻度宽表 |
| ADS | `ads_` | 面向应用的出口表 | 报表、API、服务化（可落 Doris） |

**命名对齐建议：** Kafka Topic、Flink 作业名、Paimon 表、Doris 表对同一业务对象保持 **同语义命名**，仅库名 / 前缀不同，降低跨系统沟通成本。

---

## 4. 命名规范

### 4.1 表名

```text
{层}_{业务域}_{对象}[_修饰]
```

示例：

| 表名 | 说明 |
|------|------|
| `ods_sports_bet_order` | 体育订单 CDC 最新状态 |
| `ods_sports_match` | 赛事 |
| `ods_sports_market` | 盘口 / 赔率 |
| `dwd_sports_bet_order_detail` | 订单清洗明细 |
| `dws_sports_user_bet_1d` | 用户下注日汇总 |

### 4.2 字段名

- 业务字段：沿用业务语义，统一 `snake_case`。
- 平台字段：固定命名，避免与业务字段冲突。

**ODS CDC 表推荐平台字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | `STRING` | 操作类型，作业内归一为 `I` / `U` / `D`（或兼容 Debezium `c/u/d/r` 后映射） |
| `ingest_time` | `TIMESTAMP(3)` | 进入 Flink / 湖的时间，**UTC** |
| `dt` | `DATE` | 分区字段（见第 8 节，必须稳定） |

业务时间保留原字段：`create_time`、`update_time`（语义为业务侧时间，入库统一按 UTC 存储）。

---

## 5. ODS 层规范（CDC 原始层）

### 5.1 定位

- 同步业务数据库的 **最新状态**（或按表选型保留可回溯 changelog）。
- **不做**跨表宽表、复杂指标计算。
- 支撑对账、回放、下游重刷。

典型对象：`bet_order`、`player`、`match`、`market` 等。

### 5.2 ODS 表示例（生产推荐）

```sql
CREATE TABLE ods_sports_bet_order (
    merchant_id   BIGINT,
    order_id      BIGINT,

    user_id       BIGINT,
    stake_amount  DECIMAL(18, 2),
    status        STRING,

    create_time   TIMESTAMP(3),   -- 业务创建时间，UTC
    update_time   TIMESTAMP(3),   -- 业务更新时间，UTC

    op            STRING,         -- 归一后的 I/U/D
    ingest_time   TIMESTAMP(3),   -- 入湖时间，UTC

    dt            DATE,           -- 分区：建议取 create_time 的 UTC 日期；更新不改 dt

    PRIMARY KEY (merchant_id, order_id) NOT ENFORCED
) PARTITIONED BY (dt)
WITH (
    'connector' = 'paimon',
    'bucket' = '16',
    'bucket-key' = 'merchant_id',
    'merge-engine' = 'deduplicate',
    'write.target-file-size' = '256MB',
    'changelog-producer' = 'full-compaction',
    'snapshot.time-retained' = '7d'
);
```

**硬性要求（Paimon）：**

- 多商户表 Primary Key **必须包含** `merchant_id`（或等价租户键）。
- `'bucket-key'` **必须是 Primary Key 的前缀 / 子集**。  
  错误示例：`PRIMARY KEY (order_id)` + `'bucket-key'='merchant_id'`。

---

## 6. Primary Key 规范

| 要求 | 说明 |
|------|------|
| 业务唯一 | 能唯一定位一行 |
| 稳定不变 | 不随订单状态、赔率刷新等业务流转而改变 |
| 可定位更新 | Update / Delete 依赖 PK 合并 |

| 对象 | 推荐 Primary Key |
|------|------------------|
| 订单 | `(merchant_id, order_id)` |
| 订单流水 | `(merchant_id, transaction_id)` |
| 用户 | `(merchant_id, user_id)` |
| 风控结果 | `(merchant_id, order_id)` 或业务事件唯一 ID |
| 赛事 | `(match_id)`，若租户隔离则 `(merchant_id, match_id)` |
| 盘口 / 赔率 | 真实业务唯一键（通常包含 `match_id` / `market_id`） |

**禁止**单独使用以下字段作为 PK：`create_time`、`update_time`、纯 `merchant_id`。

---

## 7. Merge Engine 规范

| 表类型 | `merge-engine` | 适用说明 |
|--------|----------------|----------|
| CDC 最新状态表 | `deduplicate` | MySQL / PG 等 insert、update、delete，按 PK 保留最新 |
| 宽表局部更新（如用户画像） | `partial-update` | 多流补齐不同字段 |
| 预聚合汇总 | `aggregation` | 明确聚合函数与指标语义后再使用 |

**删除数据：**

- 保留 CDC Delete 事件语义，由引擎按 PK 合并处理。
- 应用层 **不要**依赖“物理删除文件”完成业务删除。
- 合规硬删需求走专项流程（新表迁移 / 受控 purge），不作为日常写法。

---

## 8. Bucket 规范

### 8.1 Bucket 数量（默认起点，需结合压测调整）

| 单表数据规模（量级） | 建议 bucket |
|----------------------|------------|
| < 100GB | 4～8 |
| 100GB～1TB | 8～16 |
| 1TB～10TB | 16～32 |
| > 10TB | 32～64 |

体育订单类（多商户、高频）：默认 **`bucket = 16`**。

### 8.2 与并行度对齐

```text
Flink Sink parallelism ≈ bucket 数
```

避免并行度远小于 bucket（小文件、写倾斜）或无规划地远大于 bucket。

### 8.3 Bucket Key（多租户）

| 对象 | 推荐 `bucket-key` | 原因 |
|------|-------------------|------|
| 订单 / 流水 / 风控 / 用户 | `merchant_id` | 匹配高频 `WHERE merchant_id = ?` |
| 赛事 | `match_id` | 赛事域查询共置 |
| 赔率 / 盘口 | `match_id` | 与赛事共置 |

再次强调：`bucket-key` ⊆ `PRIMARY KEY`。

---

## 9. Partition 规范

### 9.1 统一约定

- 分区字段统一为：`dt DATE`。
- **不要**使用 `merchant_id` 作为分区键（分区爆炸、运维成本高）。
- **默认不要**使用 `hour` 分区；仅在有明确极热流与运维方案时专项评估。

### 9.2 CDC 可变状态表的分区硬约束（重点）

订单等状态频繁更新的表，若 `dt` 取自 `update_time`，更新会导致 **跨分区写入多份数据**，查询与对账极易出错。

| 表形态 | `dt` 建议 |
|--------|-----------|
| 可变 CDC 最新状态（订单、用户资料等） | 取 **稳定业务日**（如订单 `create_time` 的 UTC 日期），**更新不修改 `dt`**；中小维表可评估仅 Bucket、不分区 |
| 追加型明细（流水、事件、日志） | 可按事件时间的 UTC 日期分区 |

---

## 10. 时间规范

| 环节 | 规范 |
|------|------|
| 湖内存储 | **UTC**，类型优先 `TIMESTAMP(3)` |
| Flink 作业 | Source / Watermark / Window 统一 UTC，并在作业说明中写明 |
| 分区 `dt` | UTC 日期，与存储时区一致 |
| 展示层 | BI、API、前端按 **商户时区** 转换 |

**禁止**同一条链路上混用「业务本地时区写入」与「UTC 计算」。

---

## 11. 文件大小与 Compaction

生产环境建议显式配置（可按表级微调，但需有默认值）：

```text
'write.target-file-size' = '256MB'
'changelog-producer' = 'full-compaction'   -- 实时 CDC 表常用
'snapshot.time-retained' = '7d'            -- 按合规与回放需求调整
```

目标：控制小文件数量，降低 Compaction 与元数据压力。具体 Compaction 频率结合写入量压测确定。

---

## 12. Schema 演进规范

| 变更类型 | 态度 | 说明 |
|----------|------|------|
| 新增可空字段 | 允许 | 优先方案 |
| 补充注释 / 元数据 | 允许 | — |
| 修改字段类型 | 谨慎 | 如 `STRING` → `BIGINT`，需兼容与回填方案 |
| 删除字段、修改 PK、修改分区键 | 默认禁止 | 走新表迁移，不在线破坏性变更 |

---

## 13. Flink 写入规范

1. **Checkpoint 必须开启**（常见起点：30s～1min，按延迟与状态大小调整）。
2. 生产作业明确 Restart 策略与失败告警。
3. Sink 并行度与 Bucket 数匹配（见第 8.2 节）。
4. Exactly-once 与状态后端按部署环境启用，并在作业 Runbook 中记录。

---

## 14. 公司级对象标准（体育 SaaS）

| 对象 | Primary Key | Bucket Key | 默认 Bucket |
|------|-------------|------------|-------------|
| 订单 | `(merchant_id, order_id)` | `merchant_id` | 16 |
| 订单流水 | `(merchant_id, transaction_id)` | `merchant_id` | 16 |
| 用户 | `(merchant_id, user_id)` | `merchant_id` | 16 |
| 风控结果 | `(merchant_id, order_id)` | `merchant_id` | 16 |
| 赛事 | `(match_id)` | `match_id` | 8 |
| 赔率 / 盘口 | 业务唯一键（通常含 `match_id`） | `match_id` | 16 |

新业务对象入库前，应先在评审中补齐上表三项：**PK、Bucket Key、Bucket 数**。

---

## 15. 与 Doris 的衔接约定

1. **分层与命名**：Doris 侧继续使用 `ods_` / `dwd_` / `dws_` / `ads_`，与湖表语义对齐。
2. **多租户裁剪**：Doris 模型同样优先带 `merchant_id`，便于分区 / 分桶 / 查询裁剪。
3. **时间语义**：Doris 存储 UTC 或明确记录 `DATETIME` 时区约定；对外展示在服务层转换。
4. **管道复用**：CDC 数据优先 **Kafka 一份扇出**（入湖 / 入仓），避免两套管线两套口径。

---

## 16. 标准生产模板（可复制）

```sql
CREATE TABLE ods_{biz}_{entity} (
    merchant_id   BIGINT,
    id            BIGINT,           -- 按对象替换为 order_id / user_id 等

    -- 业务字段 ...

    create_time   TIMESTAMP(3),
    update_time   TIMESTAMP(3),
    op            STRING,
    ingest_time   TIMESTAMP(3),
    dt            DATE,             -- 稳定分区键

    PRIMARY KEY (merchant_id, id) NOT ENFORCED
) PARTITIONED BY (dt)
WITH (
    'connector' = 'paimon',
    'bucket' = '16',
    'bucket-key' = 'merchant_id',
    'merge-engine' = 'deduplicate',
    'write.target-file-size' = '256MB',
    'changelog-producer' = 'full-compaction',
    'snapshot.time-retained' = '7d'
);
```

非多租户或非订单域表，按第 14 节替换 PK / Bucket Key / Bucket，不得照搬 `merchant_id`。

---

## 17. 评审检查清单（上线前）

- [ ] 分层与表名符合第 3、4 节  
- [ ] Primary Key 稳定且唯一；多租户表含租户键  
- [ ] `bucket-key` ⊆ Primary Key；Bucket 与 Sink 并行度匹配  
- [ ] 分区 `dt` 对 CDC 可变表是 **稳定业务日**，更新不改分区  
- [ ] 时间字段与作业时区均为 UTC，文档已说明  
- [ ] 已显式配置 `write.target-file-size` 与 snapshot 保留策略  
- [ ] Schema 变更路径明确（可加列 / 禁止改 PK）  
- [ ] 下游 Doris / API 命名与时区约定已对齐  

---

## 18. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-08-06 | 首版发布：面向体育博彩 SaaS 实时湖仓团队规范 |

---

**维护建议：** 参数默认值（Bucket、文件大小、Checkpoint）允许按集群容量调整，但 **PK ⊆ 语义、分区稳定性、UTC 存储、多租户裁剪** 四条不作为“可选项”放松。
