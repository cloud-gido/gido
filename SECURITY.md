# 安全策略

## 支持的版本

| 版本 | 支持 |
|------|------|
| 最新 `main` 分支 | ✅ 安全修复 |
| 已发布 tag（如 `v1.x`） | ✅ 仅严重漏洞 |
| 更旧版本 | ❌ 不保证 |

## 报告漏洞

**请勿在公开 Issue 中披露可被利用的安全漏洞。**

请通过以下方式私下报告：

1. GitHub **Security Advisories**（若仓库已启用 Private vulnerability reporting）  
2. 或发送邮件至维护者：**troyzhujingbin@163.com**、**chenghap0712@gmail.com**

报告请尽量包含：

- 影响组件（前端 / 后端 / 集成）
- 复现步骤与 PoC（如有）
- 影响范围（RCE、越权、信息泄露等）
- 建议修复思路（可选）

## 响应预期

| 阶段 | 目标 |
|------|------|
| 确认收到 | 3 个工作日内 |
| 初步评估 | 7 个工作日内 |
| 修复或缓解方案 | 视严重程度，通常 30 天内 |

## 安全最佳实践（部署 GIDO 时）

1. **立即修改**默认账号 `admin/admin123` 与 `SECRET_KEY`  
2. **勿将** `.env`、`DS_TOKEN`、`INTERNAL_TOKEN` 提交到 Git  
3. 生产环境使用 HTTPS、网络隔离；Dolphin / Flink / Kafka 不对公网裸暴露  
4. 定期轮换 API Token 与数据库密码  
5. 启用 RBAC，遵循最小权限（`gido:batch:*` / `gido:stream:*` / `gido:service:*`）

## 已知非漏洞项

- 开发环境默认密码（文档已标注，生产必须修改）  
- 本地 `127.0.0.1` 部署无 TLS（需自行在前置网关终止 SSL）
