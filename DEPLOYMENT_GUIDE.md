# 现代大数据平台 - 完整部署与使用指南

> **说明**：仓库已聚焦 **玑渡 GIDO**；原 `bigdata-governance` 数据治理子项目已移除，编排见根目录 `docker-compose-platform.yml` 与 `gido/`。

## 📋 目录

- [系统要求](#系统要求)
- [快速开始](#快速开始)
- [架构说明](#架构说明)
- [组件部署](#组件部署)
- [配置说明](#配置说明)
- [使用指南](#使用指南)
- [监控运维](#监控运维)
- [常见问题](#常见问题)

---

## 系统要求

### 硬件要求
- **CPU**: 8核以上（推荐16核）
- **内存**: 32GB以上（推荐64GB）
- **磁盘**: 500GB SSD以上
- **网络**: 千兆以太网

### 软件要求
- **操作系统**: Linux (Ubuntu 20.04+, CentOS 8+) 或 macOS
- **Docker**: 20.10+
- **Docker Compose**: 2.0+
- **Git**: 2.30+

---

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd bigdata_all
```

### 2. 一键启动所有服务

```bash
# 赋予执行权限
chmod +x start-platform.sh

# 启动所有服务
./start-platform.sh
```

### 3. 验证部署

访问以下服务确认部署成功：

| 服务 | URL | 默认账号/密码 |
|------|-----|--------------|
| GIDO 前端 | http://localhost:3002 | 见 `init_db` 默认账号 |
| GIDO API 文档 | http://localhost:8001/docs | - |
| DolphinScheduler | http://localhost:12345/dolphinscheduler/ui | 见海豚文档 |
| Doris FE | http://localhost:8030 | root/ |
| Flink Dashboard | http://localhost:8081 | - |
| Kafka Manager | 使用kafka-tool连接 localhost:9092 | - |
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin/admin |
| Kibana | http://localhost:5601 | - |

---

## 架构说明

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│                  数据采集层                          │
│  埋点SDK / DataX / Flink CDC / Filebeat             │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────┐
│                  消息队列层                          │
│              Apache Kafka                           │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────┐
│                  流式计算层                          │
│              Apache Flink                           │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────┐
│                  数据存储层                          │
│     Doris (OLAP) / HDFS / Hive / Redis              │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────┐
│                  数据治理层                          │
│        FastAPI后端 + React前端                       │
│   资产管理 / 质量管理 / 元数据 / 任务调度 / 血缘    │
└──────────────────┬──────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────┐
│                  监控运维层                          │
│   Prometheus / Grafana / ELK / AlertManager         │
└─────────────────────────────────────────────────────┘
```

### 数据分层

- **ODS (操作数据层)**: 原始数据，与源系统保持一致
- **DWD (明细数据层)**: 清洗、标准化后的明细数据
- **DWS (汇总数据层)**: 按主题聚合的宽表
- **ADS (应用数据层)**: 面向应用的指标数据

---

## 组件部署

### 方式一：分步启动（推荐用于开发调试）

#### 1. 启动基础设施

```bash
# 启动 Kafka, Flink, 监控系统等
docker-compose -f docker-compose-infrastructure.yml up -d

# 查看服务状态
docker-compose -f docker-compose-infrastructure.yml ps
```

#### 2. 启动 Doris 数据仓库

```bash
cd doris-datawarehouse
docker-compose up -d

# 等待Doris启动完成（约2-3分钟）
docker logs -f doris-fe-leader
```

#### 3. 初始化 Doris 数仓

```bash
# 连接到Doris
mysql -h 127.0.0.1 -P 9030 -u root

# 执行初始化脚本
mysql -h 127.0.0.1 -P 9030 -u root < init-doris-warehouse.sql
mysql -h 127.0.0.1 -P 9030 -u root < init-game-warehouse.sql
```

#### 4. 启动 GIDO（与海豚同网，推荐用根目录编排）

```bash
cd /path/to/bigdata_all
cp .env.example .env   # 按需填写 GIDO_DATABASE_URL、GIDO_DS_TOKEN 等
docker compose -f docker-compose-platform.yml up -d
```

前端默认 <http://localhost:3002>，API <http://localhost:8001>。仅起 GIDO 且海豚已在宿主时，可用 `gido/docker-compose.yml`。

### 方式二：一键启动（推荐用于生产）

```bash
./start-platform.sh
```

---

## 配置说明

### 环境变量配置

编辑仓库根目录 `.env`（或 `gido/backend/.env`），与 `.env.example` 对齐，至少包含：

```bash
# 玑渡 GIDO 数据库（外部 MySQL）
GIDO_DATABASE_URL=mysql+pymysql://user:pass@host.docker.internal:3306/gido?charset=utf8mb4

# DolphinScheduler（与 docker-compose-platform.yml 同网时可省略，默认连 compose 内 dolphinscheduler-api）
# GIDO_DS_TOKEN=
```

以下为历史 Doris / Kafka 等示例配置（若你仍单独部署 Doris 基础设施时可参考；与当前 GIDO 默认编排无强绑定）：

```bash
# Doris（可选）
DORIS_HOST=fe-leader
DORIS_PORT=9030
DORIS_USER=root
DORIS_PASSWORD=

# Kafka（可选）
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
```

### Doris 配置

编辑 `doris-datawarehouse/configs/fe.conf` 和 `be.conf` 调整性能参数。

---

## 使用指南

使用 `docker compose -f docker-compose-platform.yml up -d` 启动后：

- **GIDO 前端**：<http://localhost:3002>
- **OpenAPI**：<http://localhost:8001/docs>
- **DolphinScheduler**：<http://localhost:12345/dolphinscheduler/ui>（与 `.env` 中 `GIDO_DS_*` 对齐）

以下「实时数据链路」仍以 Doris / Flink 为例，为**可选**扩展能力，与 GIDO 默认容器编排无强绑定。

### 1. 实时数据链路

#### 启动测试数据生成器

```bash
cd realtime-pipeline
pip install kafka-python
python generate_test_data.py
```

#### 查询实时数据

```sql
-- 连接到Doris
mysql -h 127.0.0.1 -P 9030 -u root

-- 查看实时数据
SELECT * FROM ods_db.ods_user_behavior_realtime ORDER BY event_time DESC LIMIT 10;

-- 查看实时看板
SELECT * FROM ads_db.ads_realtime_dashboard ORDER BY stat_time DESC LIMIT 10;
```

---

## 监控运维

### Prometheus 监控

访问 http://localhost:9090 查看指标。

常用查询：

```
# Doris BE内存使用
process_resident_memory_bytes{job="doris-be"}

# Kafka消息积压
kafka_consumer_group_lag

# API请求错误率
rate(http_requests_total{status=~"5.."}[5m])
```

### Grafana 仪表板

访问 http://localhost:3000，导入预设仪表板：

1. Doris集群监控
2. Kafka监控
3. Flink任务监控
4. 平台API监控

### 日志查询 (Kibana)

访问 http://localhost:5601 查询日志。

常用查询：

```
# 错误日志
level: ERROR

# 特定服务日志
service.name: "gido-backend"

# 慢查询
duration_seconds: >10
```

### 告警配置

编辑 `monitoring/alert_rules.yml` 配置告警规则。

告警通知支持：
- 钉钉 webhook
- 企业微信
- 邮件
- Slack

---

## 常见问题

### Q1: Doris启动失败

**问题**: FE或BE容器启动后自动退出

**解决**:
```bash
# 查看日志
docker logs doris-fe-leader

# 检查配置文件
cat doris-datawarehouse/configs/fe.conf

# 确保端口未被占用
lsof -i :8030
lsof -i :9030
```

### Q2: Kafka连接失败

**问题**: 无法连接到Kafka

**解决**:
```bash
# 检查Kafka状态
docker logs kafka-broker

# 验证Topic是否存在
docker exec -it kafka-broker kafka-topics --list --bootstrap-server localhost:9092

# 检查网络
docker network inspect bigdata_all_bigdata-network
```

### Q3: 任务调度不执行

**问题**: Cron任务到时间未执行

**解决**:
```bash
# 检查 GIDO 后端日志
docker logs gido-backend | tail -100

# 健康检查
curl -s http://localhost:8001/health
```

### Q4: 前端页面空白

**问题**: 访问前端页面显示空白

**解决**:
```bash
# 检查后端 API
curl -s http://localhost:8001/health

# 检查前端容器日志
docker logs gido-frontend
```

### Q5: 内存不足

**问题**: Docker容器因内存不足被kill

**解决**:
```bash
# 调整docker-compose中的资源限制
# 例如减少Doris BE的内存限制
memory: 4G  # 从8G改为4G

# 清理未使用的镜像和容器
docker system prune -a

# 增加Docker可用内存
# macOS: Docker Desktop -> Preferences -> Resources
```

---

## 性能优化建议

### Doris优化

1. **合理设置分桶数**: 根据数据量调整BUCKETS
2. **使用分区表**: 按时间分区，提高查询效率
3. **物化视图**: 预聚合常用查询
4. **索引优化**: 为常用过滤字段创建索引

### Flink优化

1. **调整并行度**: 根据数据量设置parallelism
2. **Checkpoint优化**: 平衡容错和性能
3. **状态后端**: 使用RocksDBStateBackend处理大状态
4. **反压监控**: 关注Flink Dashboard的反压指标

### Kafka优化

1. **分区数**: 设置为消费者数量的整数倍
2. **副本因子**: 生产环境至少设置为3
3. **保留策略**: 根据存储调整log.retention.hours
4. **批量发送**: 调整batch.size和linger.ms

---

## 安全建议

1. **修改默认密码**: 所有组件的默认密码必须修改
2. **启用TLS**: 生产环境启用SSL/TLS加密
3. **网络隔离**: 使用Docker网络隔离不同组件
4. **访问控制**: 配置RBAC权限控制
5. **审计日志**: 开启所有操作的审计日志
6. **定期备份**: 定期备份重要数据和配置

---

## 升级指南

### 升级Doris

```bash
# 1. 备份数据
docker exec doris-fe-leader mysql -u root -e "BACKUP DATABASE db_name TO 'backup_path'"

# 2. 停止服务
docker-compose down

# 3. 更新镜像版本
cd doris-datawarehouse
# 编辑docker-compose.yml，修改image版本

# 4. 重新启动
docker-compose up -d

# 5. 验证
docker logs -f doris-fe-leader
```

### 升级 GIDO

```bash
git pull origin main
docker compose -f docker-compose-platform.yml build gido-backend gido-frontend
docker compose -f docker-compose-platform.yml up -d
```

---

## 技术支持

- **文档**: 查看各组件官方文档
- **Issue**: 提交GitHub Issue
- **社区**: 加入技术交流群

---

## 附录

### A. 端口清单

| 组件 | 端口 | 用途 |
|------|------|------|
| Doris FE | 8030 | HTTP服务 |
| Doris FE | 9030 | MySQL协议 |
| Doris BE | 8040 | HTTP服务 |
| Kafka | 9092 | Broker服务 |
| Flink JobManager | 8081 | Web Dashboard（`k8s/flink.yaml` Session，LoadBalancer / port-forward 到宿主常见为 8081） |
| Flink SQL Gateway | 8083 | REST（集群内 8083；对外见 `k8s/flink-sql-gateway-ingress.yaml` / NodePort 可选件） |
| Prometheus | 9090 | 监控查询 |
| Grafana | 3000 | 可视化面板 |
| Kibana | 5601 | 日志查询 |
| Elasticsearch | 9200 | 搜索引擎 |
| Redis | 6379 | 缓存服务 |
| MinIO | 9000 | S3 API |
| MinIO Console | 9001 | Web控制台 |

### B. 目录结构

```
bigdata_all/
├── gido/                 # GIDO（前后端）
│   ├── backend/
│   └── frontend/
├── doris-datawarehouse/       # Doris 数仓（可选）
├── k8s/                       # K8s 清单（Flink / GIDO 等）
├── data/                      # 数据持久化
├── docker-compose-platform.yml
└── *.md
```

### C. 常用命令速查

```bash
# 查看所有运行中的容器
docker ps

# 查看容器日志
docker logs -f <container_name>

# 进入容器
docker exec -it <container_name> bash

# 重启服务
docker-compose restart

# 停止所有服务
docker-compose down

# 清理数据（谨慎使用）
docker-compose down -v
```
