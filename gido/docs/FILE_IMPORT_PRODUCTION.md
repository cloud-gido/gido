# 文件导入生产基线 — 兼容性与部署说明

本文档对应「文件导入生产基线」改造。**不自动执行部署**；由运维按环境启用。

## 行为变更摘要

1. **版本模型**：`dw_file_import_versions` 保存不可变文件/字段/操作模式快照；任务 `active_import_version_id` 指向生效版本。旧任务首次运行时自动从 `sync_config` 迁移。
2. **安全 API**：`PUT /integration/tasks/{id}` **禁止** file_import 任意改 `sync_config`。重新导入请走 `POST /file-import/tasks/{id}/versions`。
3. **写入模式**：`create` / `append` / `replace`。装载先入 staging，成功后再发布；append 前做 schema 兼容检查；Doris replace 依赖 `ALTER TABLE … REPLACE WITH TABLE`，不支持则明确失败（不先删表）。
4. **幂等 retry**：失败记录用同一 `execution_key`：`POST /file-import/records/{id}/retry`。成功记录不可通用 `run` 重跑。
5. **上传**：分片可选 `chunk_sha256`；finalize 分布式锁 + 内容 `content_sha256`；单用户并发上传上限；`cleanup_orphan_uploads` 可按 TTL 回收未引用文件。
6. **执行**：`SyncRecord` 以 `pending` 入队，由 `sync_worker` 认领（心跳 + 超时回收），不再用「创建即 daemon 线程」作为唯一路径。

## 配置项（`app.core.config` / 环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `FILE_IMPORT_MAX_CONCURRENT_UPLOADS` | 3 | 单用户并发 uploading |
| `FILE_IMPORT_ORPHAN_TTL_HOURS` | 72 | 未引用上传回收 |
| `FILE_IMPORT_STALE_RUNNING_MINUTES` | 120 | running 无心跳回收 |
| `FILE_IMPORT_REQUIRE_SHARED_STORAGE` | false | 生产多副本建议 `true`（需制品 S3 + Redis） |
| `FILE_IMPORT_STRICT_QUALITY_DEFAULT` | true | 默认严格质量 |

## 多副本要求

- EKS `replicas > 1` 时必须启用制品 S3 与 Redis（分片会话/合并跨 Pod）。
- 可将 `FILE_IMPORT_REQUIRE_SHARED_STORAGE=true` 作为启动守卫（仅打错误日志，避免静默单盘）。

## 迁移

启动时 `migrate_file_import_production`：

- 创建 `dw_file_import_versions`
- `dw_sync_tasks.active_import_version_id`
- `dw_sync_records`：`execution_key` / `retry_of` / `version_id` / `config_snapshot` / `phase` / `heartbeat_at` / `triggered_by` / `quality`

## 前端

- 步骤门禁：未上传/未确认字段不可跳步。
- 目标页：写入模式 + schema diff + 质量模式。
- 历史：失败显示「重试本次」；成功不可直接重跑。

## 验证建议（本地）

```bash
cd gido/backend && .venv/bin/pytest -q \
  tests/test_file_import.py \
  tests/test_file_import_version.py \
  tests/test_file_import_production.py \
  tests/test_file_import_integration.py
cd gido/frontend && npm test -- --run
cd gido/frontend && npx playwright test e2e/file-import.spec.ts
```

覆盖：分片 checksum / finalize 锁与幂等 / orphan cleanup / stale reclaim / staging publish /
versions & schema-diff API / 成功不可重跑 / 失败幂等 retry / Drawer 门禁 adoption / Playwright Drawer 打开。
真实 MySQL/Doris Stream Load 仍需有库环境；CI backend pytest 已纳入 file_import 单测与 SQLite 集成测（其它需 DB 的 integration 套件仍忽略）。
