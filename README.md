# 玑渡 GIDO · 开源大数据开发与治理平台

<p align="center">
  <strong>璇玑指引 · 数据有渡</strong><br/>
  <sub>DATA · FLOW · VALUE</sub>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/felix199103/gido/actions/workflows/ci.yml"><img src="https://github.com/felix199103/gido/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://flink.apache.org/"><img src="https://img.shields.io/badge/Flink-2.0.1-blue.svg" alt="Flink" /></a>
  <a href="https://kafka.apache.org/"><img src="https://img.shields.io/badge/Kafka-7.5.0-green.svg" alt="Kafka" /></a>
  <a href="https://gitee.com/bigdata_troy/gido"><img src="https://img.shields.io/badge/Gitee-镜像-C71D23.svg" alt="Gitee" /></a>
</p>

<p align="center">
  <img src="docs/screenshots/01-login.png" alt="GIDO 登录页" width="640" />
</p>

<p align="center">
  <em>开源大数据开发、调度与数据服务套件 · FastAPI + React · Docker 一键全栈</em>
</p>

<p align="center">
  <a href="#快速体验"><strong>快速体验</strong></a> ·
  <a href="docs/PRODUCT_OVERVIEW.md"><strong>产品概览（含截图）</strong></a> ·
  <a href="#三大子产品">三大子产品</a> ·
  <a href="gido/docs/DEPLOYMENT_SOP.md">部署文档</a>
</p>

---

## 三大子产品

一套账号，三个工作台 — 登录后通过顶部 **产品切换器** 进入不同模块：

| | 子产品 | 一句话 | 截图 |
|---|--------|--------|------|
| 📦 | **GIDO Batch**（玑渡·批） | 离线编排 · 调度派送 | [数据开发](docs/screenshots/03-batch-studio.png) |
| 🌊 | **GIDO Stream**（玑渡·流） | 实时流转 · Flink 引擎 | [作业开发](docs/screenshots/04-stream-studio.png) |
| 🔌 | **GIDO Serve**（玑渡·服） | 数据出渡 · API 网关 | [服务概览](docs/screenshots/05-serve-overview.png) |

<p align="center">
  <img src="docs/screenshots/02-product-selector.png" alt="进入 GIDO 产品选择" width="720" />
</p>

### GIDO Batch · 离线开发与治理

SQL 开发、工作流 DAG、DolphinScheduler 调度、数据集成、运维中心、发布审批，以及数据字典 / 探查 / 质量治理。

<p align="center">
  <img src="docs/screenshots/03-batch-studio.png" alt="GIDO Batch" width="880" />
</p>

### GIDO Stream · 实时流计算

Flink SQL / JAR 作业开发、运维监控、发布审批；支持多套 Flink 集群连接，默认全栈含 JM（8081）与 SQL Gateway（8083）。

<p align="center">
  <img src="docs/screenshots/04-stream-studio.png" alt="GIDO Stream" width="880" />
</p>

### GIDO Serve · 数据服务

将 SQL 封装为 HTTP API，AppKey / AppSecret 授权，提供服务概览、调用监控与开放网关。

<p align="center">
  <img src="docs/screenshots/05-serve-overview.png" alt="GIDO Serve" width="880" />
</p>

> 更多界面说明与菜单详解见 **[docs/PRODUCT_OVERVIEW.md](docs/PRODUCT_OVERVIEW.md)**。

---

## 快速体验

### 前置要求

- Docker 20.10+、Docker Compose V2
- 建议 Docker Desktop 内存 ≥ 8GB

### 一键启动

```bash
git clone https://github.com/felix199103/gido.git
cd gido

cp .env.example .env   # 可选；生产请填写 GIDO_DS_TOKEN 等

chmod +x start-platform.sh
./start-platform.sh
```

### 登录体验

| 步骤 | 操作 |
|------|------|
| 1 | 打开 **http://127.0.0.1:3002** |
| 2 | 账号 **`admin`** / 密码 **`admin123`**（生产务必修改） |
| 3 | 选择 **玑渡·批 / 流 / 服** 进入对应工作台 |
| 4 | 账号菜单 → **关于 GIDO** 查看版本与开源信息 |

### 平台服务地址

| 服务 | URL |
|------|-----|
| GIDO 前端 | http://127.0.0.1:3002 |
| GIDO API | http://127.0.0.1:8001/docs |
| DolphinScheduler | http://127.0.0.1:12345/dolphinscheduler/ui |
| Flink Web UI | http://127.0.0.1:8081 |
| Flink SQL Gateway | http://127.0.0.1:8083 |

### 常用命令

```bash
./start-platform.sh status
./start-platform.sh logs backend
./start-platform.sh stop
bash scripts/reset-gido-docker.sh   # 端口冲突时清理
```

---

## 架构与技术栈

```
┌─────────────┐   ┌──────────────┐   ┌─────────────┐
│ GIDO Batch  │   │ GIDO Stream  │   │ GIDO Serve  │
│ 离线·治理    │   │ Flink 实时   │   │ 数据 API    │
└──────┬──────┘   └──────┬───────┘   └──────┬──────┘
       │                 │                  │
       └─────────────────┼──────────────────┘
                         ▼
              FastAPI + React + PostgreSQL
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
 DolphinScheduler    Apache Flink      Apache Kafka
```

| 层级 | 技术 |
|------|------|
| 前端 | React · Vite · Ant Design |
| 后端 | FastAPI · PostgreSQL |
| 调度 | Apache DolphinScheduler |
| 流计算 | Apache Flink · SQL Gateway |
| 消息 | Apache Kafka |
| 部署 | Docker Compose |

---

## 项目结构

```
gido/                    # 仓库根
├── gido/                # 应用代码（backend + frontend）
├── dockerFile/          # 全栈 compose（PG / Kafka / Flink / Dolphin）
├── docs/
│   ├── PRODUCT_OVERVIEW.md   # 产品截图与体验指南
│   └── screenshots/
├── start-platform.sh
└── k8s/                 # 可选 K8s 部署
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/PRODUCT_OVERVIEW.md](docs/PRODUCT_OVERVIEW.md) | **产品截图与 5 分钟体验指南** |
| [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md) | 从 Git 部署与库初始化 |
| [gido/docs/TROUBLESHOOTING_SOP.md](gido/docs/TROUBLESHOOTING_SOP.md) | 按现象排障 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |

---

## 贡献与许可证

欢迎 Issue 与 Pull Request。源代码采用 **[Apache License 2.0](LICENSE)**；「玑渡 / GIDO / Logo」使用规范见 [TRADEMARK.md](TRADEMARK.md)。

---

## 维护者

- Troy · [troyzhujingbin@163.com](mailto:troyzhujingbin@163.com)
- Chenghap · [chenghap0712@gmail.com](mailto:chenghap0712@gmail.com)

[GitHub Issues](https://github.com/felix199103/gido/issues) · [Gitee 镜像](https://gitee.com/bigdata_troy/gido)
