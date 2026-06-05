# 贡献指南

感谢关注 **玑渡 GIDO** 开源项目！

## 开始之前

- 阅读 [LICENSE](LICENSE)（Apache-2.0）与 [TRADEMARK.md](TRADEMARK.md)  
- 品牌与文案规范：[gido/docs/BRAND.md](gido/docs/BRAND.md)  
- 开源发布说明：[gido/docs/OPEN_SOURCE.md](gido/docs/OPEN_SOURCE.md)

## 如何贡献

1. Fork 本仓库  
2. 创建分支：`git checkout -b feat/your-topic`  
3. 修改代码并自测（见下方）  
4. 提交 PR 到 `main`，描述变更动机与测试方式  

## 开发环境

```bash
# 后端
cd gido/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入本地配置，勿提交 .env
python init_db.py
uvicorn app.main:app --reload --port 8001

# 前端
cd gido/frontend
npm install
npm run dev
```

整栈可选：`./start-platform.sh`（仓库根目录）。

## 提交前检查

```bash
# 前端
cd gido/frontend && npm run build

# 后端（如有 pytest）
cd gido/backend && pytest -q

# SPDX 文件头（CI 会校验）
python gido/scripts/add_spdx_headers.py

# 勿引入密钥
grep -rE "(password|secret|token)\s*=" gido --include="*.env" --include="*.ts" --include="*.py" \
  | grep -v example | grep -v test_
```

## PR 规范

- **一个 PR 一个主题**（功能 / 修复 / 文档分开）  
- 用户可见文案符合 GIDO 品牌（不用旧名 DataWorks 等）  
- 涉及权限码时同步改 `backend/app/core/perm_codes.py` 与 `frontend/src/perm.ts`  
- 涉及路由时同步改 `frontend/src/routes.ts` 与文档  

## 行为准则

请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 版权与许可

提交 PR 即表示你同意你的贡献在 [Apache-2.0](LICENSE) 下授权给项目。  
若你Employer 对代码有 IP 要求，请先获得必要授权。

## 安全问题

请勿在 Issue 公开漏洞，见 [SECURITY.md](SECURITY.md)。
