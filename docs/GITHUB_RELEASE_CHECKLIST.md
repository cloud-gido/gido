# GitHub 公开发布检查清单

面向维护者：将 **玑渡 GIDO** 推送到 GitHub 公开仓库前的最后一轮自查。与 [gido/docs/OPEN_SOURCE.md](../gido/docs/OPEN_SOURCE.md) 互补，本清单侧重 **发布当日操作顺序**。

---

## 1. 法律与品牌（仓库根目录）

- [ ] [LICENSE](../LICENSE) — Apache-2.0 完整文本  
- [ ] [NOTICE](../NOTICE) — 第三方依赖摘要  
- [ ] [TRADEMARK.md](../TRADEMARK.md) — 商标与 Logo 政策  
- [ ] [SECURITY.md](../SECURITY.md) — 漏洞报告渠道  
- [ ] [CONTRIBUTING.md](../CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)  
- [ ] [CHANGELOG.md](../CHANGELOG.md) — 含 `[Unreleased]` 条目  
- [ ] README 徽章与链接指向正确 org/repo  

---

## 2. 密钥与敏感信息

```bash
# 不得提交真实 .env
git status --ignored | grep '\.env' || true

# 扫描疑似密钥（应无匹配，或仅 .example）
grep -rE "(DS_TOKEN|SECRET_KEY|INTERNAL_TOKEN)=[^[:space:]]+" gido k8s \
  --include="*.env" --include="*.yaml" --include="*.yml" \
  | grep -v '.example' || true

# 历史提交若含密钥：轮换 token 后考虑 git filter-repo
```

- [ ] 仅提交 `.env.example` / `gido/backend/.env.example` / `k8s/gido-deploy.env.example`  
- [ ] 默认密码 `admin/admin123` 在文档中标注「生产禁用」  
- [ ] 无真实 DS Token、AWS Key、数据库密码出现在 YAML/脚本/文档  

---

## 3. 源码合规

```bash
python gido/scripts/add_spdx_headers.py
git status --porcelain gido k8s/flink-sql-runner/src   # 应为空

grep -ri dataworks gido k8s docs --exclude-dir=node_modules --exclude-dir=dist \
  --exclude='MIGRATION_FROM_DATAWORKS.md' --exclude='add_spdx_headers.py' || true
```

- [ ] `gido/` 下 Python / TS / TSX 均含 `SPDX-License-Identifier: Apache-2.0`  
- [ ] `k8s/flink-sql-runner/src/**/*.java` 均含 SPDX 头  
- [ ] `gido/frontend/package.json` 含 `"license": "Apache-2.0"`  
- [ ] 无遗留 `dataworks` 命名（迁移文档除外）  

---

## 4. 构建与测试

```bash
cd gido/frontend && npm ci && npm run build
cd gido/backend && pip install -r requirements.txt && python -m compileall app
cd gido/backend && pytest -q    # 本地有 pytest 时
```

- [ ] 前端 `npm run build` 通过  
- [ ] 后端 `compileall` / `pytest` 通过  
- [ ] CI workflow（`.github/workflows/ci.yml`）在目标分支可绿  

---

## 5. 文档完整性

- [ ] [README.md](../README.md) — 定位、架构、快速体验、文档索引  
- [ ] [docs/PRODUCT_OVERVIEW.md](./PRODUCT_OVERVIEW.md) — 截图与体验路径  
- [ ] [docs/PRODUCT_MATURITY.md](./PRODUCT_MATURITY.md) — 诚实的能力边界  
- [ ] [docs/SCHEDULER_INTEGRATION.md](./SCHEDULER_INTEGRATION.md) — DS 隐藏引擎架构  
- [ ] [docs/ALERT_NOTIFICATION.md](./ALERT_NOTIFICATION.md) — 告警通知  
- [ ] [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) — 部署索引  
- [ ] [gido/docs/TROUBLESHOOTING_SOP.md](../gido/docs/TROUBLESHOOTING_SOP.md) — DS Token 401 等  

---

## 6. GitHub 仓库设置

- [ ] Visibility: **Public**  
- [ ] Default branch: `main`（或团队约定分支）  
- [ ] Branch protection: PR + CI required  
- [ ] Security → Private vulnerability reporting: 开启  
- [ ] Topics: `gido`, `big-data`, `flink`, `dolphinscheduler`, `data-platform`, `apache-2.0`  
- [ ] Description / Website: 指向文档或演示环境  
- [ ] Secrets: CI 用 `GITHUB_TOKEN` / 可选 `GIDO_DS_TOKEN`，**勿写入 workflow 明文**  

---

## 7. 打 Tag 与 Release

```bash
git tag -a v1.1.0 -m "GIDO v1.1.0"
git push origin v1.1.0
```

- [ ] 更新 `gido/backend/app/core/config.py` 中 `APP_VERSION`（若对外展示）  
- [ ] GitHub Release 附：变更摘要、Compose/K8s 快速启动、已知局限  
- [ ] （可选）推送 `ghcr.io/<org>/gido-backend` 等镜像  

---

## 8. 产品成熟度自评（发布前必读）

| 维度 | 状态 | 说明 |
|------|------|------|
| 架构 | ✅ 清晰 | GIDO 实例中心 + DS 隐藏引擎 |
| Stream / Serve | ✅ 生产可落地 | Operator + S3 + Serve 完整 |
| Batch 调度 | ⚠️ 需外置 DS | K8s 默认不 bundled Dolphin |
| 告警通知 | ✅ 可用 | 邮件/Webhook/飞书/企微；需自行配 SMTP |
| 测试 | ⚠️ 中等 | 单元测试有；E2E 与 DS 集成测试待加强 |
| 元库持久化 | ⚠️ 示例用 emptyDir | 生产须 PVC / RDS |
| DS Token | ⚠️ 运维敏感 | 过期会导致 401，文档已覆盖 |

**结论**：适合作为 **开源数据平台种子项目** 与 **批流服一体化参考实现** 发布；生产 Batch 调度需规划外置 Dolphin 与 Token 轮换。

---

## 9. 发布后

- [ ] 监控 Issues / Security Advisories  
- [ ] 回复社区 PR 时引用 [CONTRIBUTING.md](../CONTRIBUTING.md)  
- [ ] 重大变更写入 [CHANGELOG.md](../CHANGELOG.md)  
