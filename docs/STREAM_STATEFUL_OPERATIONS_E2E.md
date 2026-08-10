# GIDO Stream 状态化运维 E2E 清单

本清单验证“开发提交版本、运维部署运行、Savepoint 停止与恢复”的完整链路。默认只在测试集群执行；不得把本清单当作生产部署授权。

## 前置条件

- Flink Kubernetes Operator 1.15.x 与 Flink 2.0.x CRD 已安装。
- `FLINK_OPERATOR_NAMESPACE` 指向测试作业命名空间；GIDO ServiceAccount 对该命名空间具有：
  - `flinkdeployments` / `flinksessionjobs`：`get/list/create/patch/delete`
  - **`flinkstatesnapshots`：`get/list/watch`（建议与上相同 verbs）** — Operator 1.15 计划停止依赖此 CR，缺权会 403 导致平台误判超时。
- `FLINK_OPERATOR_CHECKPOINT_DIR` 与 `FLINK_OPERATOR_SAVEPOINT_DIR` 指向 JM/TM 可读写的持久对象存储。
- 部署出的 FlinkDeployment `flinkConfiguration` 须同时可见：
  - `state.checkpoints.dir`
  - **`state.savepoints.dir`**（官方键；仅有 `execution.checkpointing.savepoint-dir` 不够）
- 测试 SQL/JAR 使用稳定 operator UID；输入源可重复构造数据，输出端可以核对恢复前后的连续性。
- Savepoint 超时排障见 [FLINK_OPERATOR_FAQ.md §8](./FLINK_OPERATOR_FAQ.md#8-qa计划停止-savepoint-一直超时checkpoint-却正常)。

## SQL 作业

1. 在作业开发创建 SQL 作业，保存版本并提交。
2. 确认提交后没有创建 FlinkDeployment，运维页显示“待部署”与已批准 release。
3. 在运维页选择 release 和资源规格后部署。
4. 确认：
   - CR 位于 `FLINK_OPERATOR_NAMESPACE`；
   - GIDO 显示运行中的 release；
   - JM/TM 资源与并行度和弹窗一致；
   - 操作历史包含成功 deploy。
5. 写入一批可识别数据，记录有状态算子当前结果。
6. 点击默认「保存并停止」，确认过渡状态依次包含「正在保存状态 / 正在挂起」，最后为已停止。
7. 确认：
   - CR 仍存在且 `spec.job.state=suspended`；
   - CR `flinkConfiguration` 含 `state.savepoints.dir`；
   - 命名空间内出现**本次**新的 `FlinkStateSnapshot`（COMPLETED + path），或 `upgradeSavepointPath` 已更新；
   - 恢复点历史存在成功 Savepoint 路径（与 Snapshot/路径一致）；
   - **产品契约**：保存失败时作业仍为「运行中」（操作记录失败），不静默无状态停止，也不长期「停止失败待确认」；
   - 若仅有历史 Snapshot、本次超时：不得把旧 COMPLETED 路径记为本轮成功。
8. 选择最近恢复点重启，同时调整 TM 副本或并行度。
9. 确认恢复后结果连续、没有从头消费，运行版本和资源参数正确。
10. 再次「保存并停止」，选择历史恢复点重启，确认选定路径被使用。

## JAR 作业

重复 SQL 作业链路，并额外验证：

- release 固定 JAR artifact/version、main class、program args 和依赖版本；
- 提交后上传的新 JAR 版本不会改变已提交 release；
- 从 Savepoint 恢复时 JAR/Flink 版本兼容性信息可见；
- 不兼容状态恢复失败时保留错误和操作审计，不静默无状态启动。

## Kafka → Paimon 数据管道

### 标准 JSON / Avro 链路

1. 创建 Kafka 与 Paimon 连接配置，确认接口和页面均不返回 Secret 明文。
2. 创建 Pipeline，选择 append 或 upsert 语义，配置 Topic、consumer group、显式 Schema Contract、主键、分区和 bucket。
3. 执行 Preflight，确认：
   - Kafka Topic、Schema Registry（如使用）和 Paimon warehouse 可访问；
   - Kafka partition 与并行度/slots 容量一致；
   - 生成 SQL、编译器版本和 spec hash 可见，敏感值已脱敏；
   - Paimon 新表 DDL 或已有表 Schema Diff 准确。
4. 提交不可变 Release，确认提交本身不创建 FlinkDeployment。
5. 在作业运维部署，持续写入可识别消息并核对 Paimon snapshot、行数和主键更新语义。
6. Savepoint 停止后调整并行度，从指定恢复点重启，确认 Kafka offset 和 Paimon 结果连续且没有重复业务主键。

### CDC 与 Schema 演进

1. 分别使用 Debezium/Canal 合法 INSERT、UPDATE、DELETE 事件，确认 Paimon 主键表结果正确。
2. 新增兼容列或执行允许的类型拓宽，确认 Schema Diff 被记录，按策略审批后演进且无需无状态启动。
3. 测试删除列、重命名列、修改主键或分区，确认发布被阻断，平台不得静默执行危险 ALTER。
4. 对无 schema 的普通 JSON 仅允许生成候选 Contract；未经确认不得自动创建生产表。

### 放置、指标与错误策略

- 高 SLA、有状态、自定义依赖或不同安全域的 Pipeline 必须得到 `dedicated` 决策。
- 只有同运行时、同安全域、兼容 checkpoint 配置的低流量 Pipeline 才可建议 `grouped`；运行中不得自动改组。
- 核对 Kafka current/end offset、lag、输入/输出速率、checkpoint 时长/失败、重启次数、反压和 Paimon commit 指标。
- `fail-fast` 遇到坏消息应失败并保留定位信息；只有受控 Runner 支持原始 bytes DLQ 时才可启用 `quarantine-and-continue`。
- DLQ 消息必须带 topic/partition/offset/schema-id/error 上下文；重放动作必须有权限校验、幂等键和操作审计。

## 高风险路径

- “无状态启动”必须二次确认，操作历史记录 `stateless`。
- `allowNonRestoredState` 默认关闭；开启后必须显示会丢弃无法映射状态的警告。
- 默认「保存并停止」失败后，作业应仍显示运行中；「清理集群（丢弃状态）」在「更多」中且必须单独确认；执行后 CR 才允许删除。
- 没有成功恢复点时，「最近 Savepoint」不可选，但无状态启动仍可按权限显式执行。

## 权限

| 权限 | 预期 |
|------|------|
| READ | 查看作业、部署、恢复点和操作历史 |
| WRITE | 编辑、保存版本、提交/提交审批 |
| RUN | 部署、保存并停止、恢复、重启、清理集群 |

## 回归

- 状态同步只读取集群并回填数据库，不应隐式 patch/delete CR。
- 旧资源 URL 仍可访问，但菜单位于“作业运维”下。
- Studio 草稿自动保存不写版本历史；“保存版本”仍写历史。
- Session/K8s Application 遗留模式在关闭兼容开关时不出现在新运维交互中。
- **计划停止**：backend 可 list `flinkstatesnapshots`；不得只依赖空的 `savepointInfo`；Checkpoint 成功不能当作 Savepoint 配置已完备。排障见 [FLINK_OPERATOR_FAQ §8](./FLINK_OPERATOR_FAQ.md#8-qa计划停止-savepoint-一直超时checkpoint-却正常)。
