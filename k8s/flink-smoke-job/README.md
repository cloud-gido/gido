# Flink Smoke JAR（Operator 测试）

轻量 DataStream 作业，用于验证 GIDO → Flink Operator → 运行时镜像全链路。

- **不依赖**：Kafka、MySQL、S3、Paimon、checkpoint
- **Main Class**：`com.gido.flink.smoke.SmokeStreamJob`
- **输出**：TaskManager 日志中 `gido-smoke-jar event=...`

## 构建

Flink **2.2.1** 运行时（默认）：

```bash
cd k8s/flink-smoke-job
mvn -q -DskipTests package
# 产物：target/flink-smoke-job-1.0.0.jar
```

Flink **1.17.2** 运行时：

```bash
mvn -q -DskipTests package -Dflink.version=1.17.2
```

## GIDO 提交

1. ConfigMap 建议（冒烟）：

   ```yaml
   FLINK_OPERATOR_UPGRADE_MODE: "stateless"
   # 不配 CHECKPOINT / SAVEPOINT / S3 warehouse
   ```

2. GIDO UI → **实时开发** → 新建 **JAR** 作业：
   - 上传 `flink-smoke-job-1.0.0.jar`
   - **入口类**：`com.gido.flink.smoke.SmokeStreamJob`
   - 提交模式：**Flink Operator**
   - Parallelism：`1`

3. 验证：

   ```bash
   kubectl -n flink logs -l app=<deployment-name>,component=taskmanager -f | grep gido-smoke-jar
   ```

## 可选参数（FlinkDeployment env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `GIDO_SMOKE_ROWS` | `200` | 事件条数；`0` = 无限流 |
| `GIDO_SMOKE_INTERVAL_MS` | `500` | 每条间隔（毫秒） |

## 与 SQL 冒烟对比

| 方式 | 文件 | 适用 |
|------|------|------|
| SQL | 仓库根 `streaming-test.sql` | SqlRunner / SQL Operator |
| JAR | 本模块 | JAR Operator、自定义 Main Class |
