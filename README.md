# 玑渡 GIDO · 开源大数据开发与治理平台

> **璇玑指引 · 数据有渡** — DATA · FLOW · VALUE

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/bigdata_troy/gido/actions/workflows/ci.yml/badge.svg)](https://github.com/bigdata_troy/gido/actions/workflows/ci.yml)
[![Flink](https://img.shields.io/badge/Flink-2.0.1-blue.svg)](https://flink.apache.org/)
[![Kafka](https://img.shields.io/badge/Kafka-7.5.0-green.svg)](https://kafka.apache.org/)

**玑渡 GIDO** 是开源大数据开发与治理平台（FastAPI + React），集成离线开发、实时流计算与数据服务，默认通过 Docker Compose 一键拉起 PostgreSQL、Kafka、Flink、DolphinScheduler 与 GIDO 前后端。

| 子产品 | 路由 | 说明 |
|--------|------|------|
| **GIDO Batch**（玑渡·批） | `/gido/batch/*` | 离线开发、工作流、调度、数据集成 |
| **GIDO Stream**（玑渡·流） | `/gido/stream/*` | Flink 实时 SQL / JAR |
| **GIDO Serve**（玑渡·服） | `/gido/service/*` | 数据服务 API 与开放网关 |

---

## 快速开始

### 前置要求

- Docker 20.10+、Docker Compose V2
- 建议 Docker Desktop 内存 ≥ 8GB（同机跑 Dolphin + Flink + Kafka）

### 一键启动

```bash
git clone https://github.com/bigdata_troy/gido.git
cd gido

cp .env.example .env   # 可选；生产环境请填写 GIDO_DS_TOKEN 等

chmod +x start-platform.sh
./start-platform.sh
```

### 访问地址

| 服务 | URL |
|------|-----|
| GIDO 前端 | http://127.0.0.1:3002 |
| GIDO API / OpenAPI | http://127.0.0.1:8001/docs |
| DolphinScheduler | http://127.0.0.1:12345/dolphinscheduler/ui |
| Flink Web UI | http://127.0.0.1:8081 |
| Flink SQL Gateway | http://127.0.0.1:8083 |

默认管理员：`admin` / `admin123`（由 `GIDO_BOOTSTRAP_ADMIN_PASSWORD` 控制，**生产务必修改**）。

### 常用命令

```bash
./start-platform.sh status          # 容器状态
./start-platform.sh logs backend    # 后端日志
./start-platform.sh stop            # 停止（保留卷）
./start-platform.sh down            # 下线容器
./start-platform.sh restart         # 重启
bash scripts/reset-gido-docker.sh   # 端口/容器冲突时清理 GIDO
```

---

## 项目结构

```
gido/                              # 仓库根（git clone 后的目录）
├── gido/                          # GIDO 主应用（backend + frontend）
│   └── docs/DEPLOYMENT_SOP.md
├── dockerFile/
│   └── docker-compose.platform.yml
├── docker-compose-platform.yml
├── start-platform.sh
├── scripts/
└── k8s/
```

**仅启动 GIDO**（需自备 PostgreSQL，勿与全栈同时跑）：

```bash
cd gido && ./start.sh
```

---

## 配置

复制 `.env.example` 为 `.env`。常用变量：

| 变量 | 说明 |
|------|------|
| `GIDO_DS_TOKEN` | DolphinScheduler API Token |
| `GIDO_BIND_HOST` | 默认 `0.0.0.0`，局域网可访问 3002 |
| `GIDO_UI_PORT` / `GIDO_API_PORT` | 前端 3002、API 8001 |
| `KAFKA_LAN_HOST` | Kafka 外网广播地址（脚本会自动检测本机 IP） |

详见 [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md) 与 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)。

---

## 本地开发

```bash
# 后端
cd gido/backend
pip install -r requirements.txt
python init_db.py
uvicorn app.main:app --reload --port 8001

# 前端（dev 端口 3003，与 Docker 3002 错开）
cd gido/frontend
npm install
npm run dev
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [gido/README.md](gido/README.md) | GIDO 子项目说明 |
| [gido/docs/DEPLOYMENT_SOP.md](gido/docs/DEPLOYMENT_SOP.md) | 从 Git 部署与库初始化 |
| [gido/docs/TROUBLESHOOTING_SOP.md](gido/docs/TROUBLESHOOTING_SOP.md) | 按现象排障 |
| [gido/docs/OPEN_SOURCE.md](gido/docs/OPEN_SOURCE.md) | 开源发布与安全自查 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |

---

## 贡献与许可证

欢迎提交 Issue 与 Pull Request。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

| 文档 | 说明 |
|------|------|
| [LICENSE](LICENSE) | 源代码：**Apache License 2.0** |
| [TRADEMARK.md](TRADEMARK.md) | 「玑渡 / GIDO / Logo」商标政策 |
| [SECURITY.md](SECURITY.md) | 安全漏洞报告流程 |

---

## 维护者

- Troy · [troyzhujingbin@163.com](mailto:troyzhujingbin@163.com)
- Chenghap · [chenghap0712@gmail.com](mailto:chenghap0712@gmail.com)

问题反馈请使用 [GitHub Issues](https://github.com/bigdata_troy/gido/issues)。国内镜像：[Gitee](https://gitee.com/bigdata_troy/gido)。
