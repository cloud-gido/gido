# 玑渡 GIDO 开源发布指南

面向维护者与贡献者：如何将 GIDO 以开源形态对外发布、合规与防侵权要点。

---

## 1. 法律与文档（仓库根目录）

| 文件 | 说明 |
|------|------|
| [LICENSE](../LICENSE) | Apache-2.0 源代码授权 |
| [NOTICE](../NOTICE) | 第三方依赖与版权摘要 |
| [TRADEMARK.md](../TRADEMARK.md) | **商标与 Logo 使用政策**（防冒用官方品牌） |
| [SECURITY.md](../SECURITY.md) | 漏洞报告流程 |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献规范 |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | 行为准则 |

**要点**：Apache-2.0 允许 fork 与商用；**不**自动授权「玑渡 / GIDO / 官方 Logo」商标。

---

## 2. 发布前代码检查清单

### 2.1 密钥与隐私

```bash
# 不得提交真实 .env
git status --ignored | grep '\.env'

# 扫描疑似密钥（示例）
grep -rE "(DS_TOKEN|SECRET_KEY|INTERNAL_TOKEN)=\S+" gido --include="*.env" --include="*.yml"

# 若历史曾提交过密钥：轮换 token 并清理 git 历史（filter-repo）
```

- 仅提交 `.env.example` / `gido/backend/.env.example`  
- 本地 `gido/backend/.env` 若含真实 `DS_TOKEN` / `INTERNAL_TOKEN`，**开源前必须轮换**  
- 日志、数据目录、`.venv`、`node_modules`、`dist`、`*.db` 不入库  

### 2.2 默认凭据

- 开发默认 `admin/admin123` 必须在文档标注「生产禁用」  
- `SECRET_KEY` 生产必须随机生成  
- Compose 示例密码仅用于本地 demo  

### 2.3 品牌一致性

```bash
grep -ri dataworks gido --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=.venv
```

应无结果。规范见 [BRAND.md](./BRAND.md)。

### 2.4 构建验证

```bash
cd gido/frontend && npm run build
cd gido/backend && pytest -q   # 如有
```

---

## 3. 版本与发布

1. 更新 `gido/backend/app/core/config.py` 中 `APP_VERSION`（如需要）  
2. 编写 [CHANGELOG.md](../CHANGELOG.md) 条目  
3. 打 tag：`git tag -a v1.0.0 -m "GIDO v1.0.0"`  
4. GitHub Release：附二进制/ Docker 说明  
5. （可选）推送镜像：`gido-backend:v1.0.0`、`gido-frontend:v1.0.0`  

---

## 4. GitHub 仓库设置建议

- **Visibility**: Public  
- **Security → Private vulnerability reporting**: 开启  
- **Branch protection**: `main` 需 PR + CI  
- **Secrets**: CI 使用 `GIDO_DS_TOKEN` 等，勿写入 workflow 明文  
- **Topics**: `gido`, `big-data`, `flink`, `dolphinscheduler`, `data-platform`  

---

## 5. 防侵权：代码 vs 商标

| 手段 | 作用 |
|------|------|
| Apache-2.0 + NOTICE | 规范代码再分发，保留版权声明 |
| TRADEMARK.md | 限制冒用「玑渡 GIDO」名称与官方 Logo |
| 商标注册（可选） | 法律层保护品牌，需自行申请 |
| SECURITY.md + 快速响应 | 降低安全事件对品牌声誉损害 |

Fork 产品应使用**自有品牌**，代码致谢「Based on GIDO」即可。

---

## 6. 版权声明模板（新文件可选）

```text
Copyright 2026 玑渡 GIDO Contributors
SPDX-License-Identifier: Apache-2.0
```

Python / TypeScript 源文件可在文件头添加上述两行（非强制，但推荐）。批量补齐：

```bash
python gido/scripts/add_spdx_headers.py
```

CI 会在 PR 中校验 `gido/` 源码均已包含 SPDX 头。

---

## 7. 相关文档

- [MIGRATION_FROM_DATAWORKS.md](./MIGRATION_FROM_DATAWORKS.md) — 历史命名迁移  
- [DEPLOYMENT_SOP.md](./DEPLOYMENT_SOP.md) — 部署  
- [BRAND.md](./BRAND.md) — 品牌视觉  
