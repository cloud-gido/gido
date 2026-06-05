# 玑渡 GIDO · 开源大数据开发与治理平台

> **璇玑指引 · 数据有渡** — DATA · FLOW · VALUE  
> 基于 Apache Doris + Flink + Kafka 的开源大数据开发与治理套件

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/your-org/bigdata_all/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/bigdata_all/actions/workflows/ci.yml)
[![GIDO](https://img.shields.io/badge/GIDO-开源-orange.svg)](#)
[![Doris](https://img.shields.io/badge/Doris-2.0.3-orange.svg)](https://doris.apache.org/)
[![Flink](https://img.shields.io/badge/Flink-2.0.1-blue.svg)](https://flink.apache.org/)
[![Kafka](https://img.shields.io/badge/Kafka-7.5.0-green.svg)](https://kafka.apache.org/)

---

## 📖 项目简介

**玑渡 GIDO** 是面向企业与社区的开源大数据平台，名字取自 **璇玑、天玑星、北斗导航** 与 **数据流转（渡）** 的意象。产品由三个子套件组成：

| 子产品 | 说明 |
|--------|------|
| **GIDO Batch**（玑渡·批） | 离线开发、工作流、调度、数据集成与治理 |
| **GIDO Stream**（玑渡·流） | Flink 实时 SQL / JAR 开发与运维 |
| **GIDO Serve**（玑渡·服） | 数据服务 API、应用授权与开放网关 |

集成了数据采集、存储、计算、治理、监控等全链路能力，支持实时和离线数据处理。

### ✨ 核心特性

- 🔄 **实时数据链路**: 埋点 → Kafka → Flink → Doris，毫秒级延迟
- 📊 **OLAP引擎**: 基于Apache Doris，亿级数据亚秒级查询
- 🎯 **数据治理**: 资产管理、质量检查、元数据管理、血缘追踪
- ⚙️ **任务调度**: 支持SQL/Python/Shell任务，Cron定时调度，依赖管理
- 🌊 **工作流编排**: 可视化DAG编辑器，支持复杂业务流程
- 🔗 **数据集成**: 多数据源同步（MySQL/PostgreSQL/Doris/Kafka）
- 📈 **监控告警**: Prometheus + Grafana + ELK，全方位监控
- 🔐 **权限控制**: RBAC角色权限，数据行级/列级权限

---

## 🏗️ 技术架构

### 技术栈

| 层级 | 技术选型 |
|------|----------|
| **数据采集** | 埋点SDK, DataX, Flink CDC, Filebeat |
| **消息队列** | Apache Kafka, Schema Registry |
| **流式计算** | Apache Flink |
| **数据存储** | Apache Doris, HDFS, Redis, MinIO |
| **数据治理** | FastAPI, React, PostgreSQL |
| **任务调度** | APScheduler, Apache DolphinScheduler |
| **监控告警** | Prometheus, Grafana, ELK, AlertManager |
| **基础设施** | Docker, Docker Compose, Nginx |

### 架构图

```
┌──────────────────────────────────────────────────────┐
│                   数据采集层                          │
│   Web/App埋点  IoT设备  业务DB  日志文件              │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   消息队列层                          │
│              Apache Kafka                             │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   流式计算层                          │
│              Apache Flink                             │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   数据存储层                          │
│    Doris(OLAP)  HDFS  Hive  Redis  MinIO             │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   数据治理层                          │
│   资产管理  质量管理  元数据  血缘  调度  集成        │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   数据服务层                          │
│      API网关  BI报表  即席查询  数据导出              │
└──────────────────┬───────────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────────┐
│                   监控运维层                          │
│   Prometheus  Grafana  ELK  AlertManager             │
└──────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 8核CPU / 32GB内存 / 500GB磁盘（最低配置）

### 一键启动

```bash
# 克隆项目
git clone <your-repo-url>
cd bigdata_all

# 赋予执行权限并启动
chmod +x start-platform.sh
./start-platform.sh
```

### 访问服务

启动成功后，访问以下服务：

| 服务 | URL | 说明 |
|------|-----|------|
| GIDO 前端 | http://localhost:3002 | Compose 默认端口 |
| GIDO API | http://localhost:8001/docs | OpenAPI |
| DolphinScheduler | http://localhost:12345/dolphinscheduler/ui | 与平台 compose 同启 |
| Flink Dashboard（可选） | http://localhost:8081 | K8s/本地 JM |
| Doris FE（可选） | http://localhost:8030 | 需单独部署 Doris 时 |

其它 Prometheus / Grafana 等见 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) 与 `docker-compose-infrastructure.yml`。

详细部署指南请查看 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

---

## 📚 玑渡 GIDO 与相关能力

本仓库当前以 **玑渡 GIDO**（`gido/` + `docker-compose-platform.yml` 内 DolphinScheduler / Redis）为主。功能与接口以 **http://localhost:8001/docs** 与前端 **http://localhost:3002** 为准。

**仅从 Git 拉取并部署 GIDO（含数据库初始化）的标准流程**：见 [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md)。

Doris、实时管道、独立监控栈等为可选参考实现，见各专题文档（如 [REALTIME_DATA_PIPELINE.md](REALTIME_DATA_PIPELINE.md)），不与 GIDO 默认 Compose 强绑定。

---

## 📊 实时数据链路示例

### 架构

```
用户行为 → 埋点SDK → Kafka → Flink ETL → Doris实时表 → BI展示
```

### 快速体验

```bash
# 1. 启动实时链路
./start-realtime-pipeline.sh

# 2. 运行测试数据生成器
cd realtime-pipeline
python generate_test_data.py

# 3. 查询实时数据
mysql -h 127.0.0.1 -P 9030 -u root -e "
  SELECT * FROM ods_db.ods_user_behavior_realtime 
  ORDER BY event_time DESC LIMIT 10;
"

# 4. 查看实时看板
mysql -h 127.0.0.1 -P 9030 -u root -e "
  SELECT * FROM ads_db.ads_realtime_dashboard;
"
```

详细实现请查看 [REALTIME_DATA_PIPELINE.md](REALTIME_DATA_PIPELINE.md)

---

## 📁 项目结构

```
bigdata_all/
├── gido/                   # 玑渡 GIDO（FastAPI + 前端）
│   ├── backend/
│   └── frontend/
├── doris-datawarehouse/         # Doris 数仓配置（可选）
├── k8s/                         # Flink / GIDO 等 K8s 清单
├── data/                        # 数据持久化目录
├── queries/                     # SQL 查询示例
├── BIGDATA_PLATFORM_ARCHITECTURE.md
├── REALTIME_DATA_PIPELINE.md
├── DEPLOYMENT_GUIDE.md
└── README.md
```

---

## 🔧 配置说明

### 环境变量

根目录 `.env`（参考 `.env.example`），或开发时在 `gido/backend/.env` 覆盖：

```bash
GIDO_DATABASE_URL=mysql+pymysql://root:password@localhost:3306/gido?charset=utf8mb4
REDIS_URL=redis://localhost:6379/0
```

生产环境请使用强密码，并通过 `docker-compose-platform.yml` 或 K8s 注入密钥。

---

## 📈 性能指标

- **数据采集**: 支持 10万+ TPS 实时摄入
- **查询性能**: 亿级数据亚秒级响应
- **任务调度**: 支持 1000+ 并发任务
- **数据质量**: 99.9% 数据准确率
- **系统可用性**: 99.95% SLA

---

## 🛠️ 开发指南

### 后端开发

```bash
cd gido/backend

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

# API 文档: http://localhost:8001/docs
```

### 前端开发

```bash
cd gido/frontend

npm install
npm run dev

# 开发服务器端口以 package.json / Vite 配置为准
```

---

## 🤝 贡献指南

欢迎 Issue 与 Pull Request。请先阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md) — 开发流程与 PR 规范  
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — 行为准则  
- [gido/docs/OPEN_SOURCE.md](gido/docs/OPEN_SOURCE.md) — 开源发布与安全自查  
- [CHANGELOG.md](CHANGELOG.md) — 版本变更记录  

---

## 📄 许可证与品牌

| 文档 | 说明 |
|------|------|
| [LICENSE](LICENSE) | 源代码：**Apache License 2.0** |
| [NOTICE](NOTICE) | 第三方依赖版权摘要 |
| [TRADEMARK.md](TRADEMARK.md) | **「玑渡 / GIDO / Logo」商标政策**（代码可 fork，品牌不可冒用） |
| [SECURITY.md](SECURITY.md) | 安全漏洞私下报告流程 |

Fork 与商用代码请遵守 Apache-2.0；使用官方名称与 Logo 见 [TRADEMARK.md](TRADEMARK.md)。

---

## 👥 技术支持

- 📖 **文档**: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- 🐛 **问题反馈**: 提交 GitHub Issue
- 💬 **技术交流**: 加入微信群/QQ群

---

## 🌟 Star History

如果这个项目对你有帮助，请给我们一个 Star ⭐

---

## 📞 联系我们

- **维护者**: Troy · [troyzhujingbin@163.com](mailto:troyzhujingbin@163.com) · Chenghap · [chenghap0712@gmail.com](mailto:chenghap0712@gmail.com)

---

**Made with ❤️ by Troy & Chenghap · 玑渡 GIDO**
