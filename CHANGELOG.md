# Changelog

本文件记录 **玑渡 GIDO** 对外发布的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added

- **关于页**（`/about`）：版本、Apache-2.0、商标说明与仓库文档链接
- **GitHub Actions CI**：前端构建、后端 pytest、开源合规检查（dataworks 扫描、SPDX 头）
- **SPDX 批量脚本**：`gido/scripts/add_spdx_headers.py`
- 登录页 Apache-2.0 开源声明页脚
- 根目录 `.gitignore` 扩充（密钥、构建产物、依赖目录）

## [1.0.0] - 2026-05-20

### Added

- **GIDO Batch / Stream / Serve** 三件套：离线开发、Flink 实时、数据服务
- RBAC、工作流编排、发布审批、运维概览与集成（DolphinScheduler / Flink / Kafka）
- 品牌资产：官方 Logo、星座星徽、favicon 套件
- 开源合规文档：`LICENSE`（Apache-2.0）、`NOTICE`、`TRADEMARK.md`、`SECURITY.md`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`
- 发布指南：`gido/docs/OPEN_SOURCE.md`

### Changed

- 全量重命名：`dataworks` → **gido**（路由、权限码、环境变量、数据库、容器名）
- 统一品牌为 **玑渡 GIDO**；登录页改为浅色小清新风格
- 默认元数据库名：**`gido`**

### Removed

- 不再保留 `dataworks` 命名与兼容路径

### Security

- 提供 `.env.example` 模板；真实 `.env` 与 token 不得提交仓库
- 生产环境须修改默认 `admin` 密码与 `SECRET_KEY`

[Unreleased]: https://github.com/felix199103/gido/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/felix199103/gido/releases/tag/v1.0.0
