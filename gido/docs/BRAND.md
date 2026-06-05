# 玑渡 GIDO 品牌规范

> 前后端用户可见文案、文档、运维输出须与本规范一致。  
> 代码常量：**前端** `frontend/src/branding.ts` · **后端** `backend/app/core/brand.py`

---

## 1. 主品牌

| 场景 | 用法 |
|------|------|
| 中文主名 | **玑渡** |
| 英文主名 | **GIDO** |
| 完整展示 | **玑渡 GIDO**（登录、关于、文档标题） |
| 英文 Tagline | **DATA · FLOW · VALUE** |
| 中文品牌语 | **璇玑指引 · 数据有渡** |
| 一句话定位 | 开源大数据开发、调度与数据服务套件 |

名源：**璇玑 · 天玑星 · 北斗导航 · 数据流转（渡）**。

---

## 2. 子产品（三件套）

| 内部 ID | 英文名 | 中文短名 | 能力域 | 路由前缀 |
|---------|--------|----------|--------|----------|
| `batch` | **GIDO Batch** | 玑渡·批 | 离线批处理 | `/gido/batch/*` |
| `stream` | **GIDO Stream** | 玑渡·流 | 实时流计算 | `/gido/stream/*` |
| `service` | **GIDO Serve** | 玑渡·服 | 数据服务 | `/gido/service/*` |

**禁止**在用户界面混用旧名：GIDO、DataBatch、DataStream、DataService。

---

## 3. 视觉资产

**官方设计稿**（动态星座轨道 + GIDO 字标）：  
https://chatgpt.com/s/m_6a21668d7270819184ecaf987e3743fa

| 文件 | 用途 |
|------|------|
| `frontend/public/brand/gido-logo.png` | **主 Logo**（登录 / 关于 / 文档，含字标与 tagline） |
| `frontend/public/favicon.svg` | **浏览器标签 favicon**（浅底星徽，与登录页一致） |
| `frontend/public/apple-touch-icon.svg` | iOS 主屏图标（同上） |
| `frontend/public/brand/gido-mark.svg` | 星座星徽（产品启动器 / 侧栏小标 / 登录） |
| `frontend/public/brand/gido-logo.svg` | 矢量横版备用 |
| `frontend/public/brand/gido-batch-mark.svg` | 批侧栏（轨道 + 编排） |
| `frontend/public/brand/gido-stream-mark.svg` | 流侧栏（轨道 + 流转） |
| `frontend/public/brand/gido-service-mark.svg` | 服侧栏（轨道 + 网关） |

**设计语言**：橙金→电蓝渐变轨道、轨道内星座连线、右上主星（天玑）；字标 G 带橙色缺口。

**商标与 Logo**：官方名称与 Logo 受 [TRADEMARK.md](../../TRADEMARK.md) 约束；代码可 fork，品牌不可冒用。

主题色（与官方稿对齐，CSS 变量）：

- 批：`--dw-accent-batch` → `#ff8c42`（橙，能量/调度）
- 流：`--dw-accent-stream` → `#2dd4bf`（青，流转）
- 服：`--dw-accent-service` → `#38bdf8`（蓝，出渡/网关）

---

## 4. 统一技术命名

| 类型 | 示例 |
|------|------|
| 项目目录 | `gido/` |
| 元数据库 | PostgreSQL 库名 **`gido`** |
| 容器 | `gido-backend` / `gido-frontend` |
| 路由 | `/gido/batch/*` · `/gido/stream/*` · `/gido/service/*` |
| 权限码 | `gido:batch:studio:read` · `gido:stream:read` · `gido:service:read` |
| 环境变量 | `GIDO_*`（如 `GIDO_DATABASE_URL`） |
| Dolphin 项目 | `DS_PROJECT_NAME=GIDO` |

---

## 5. 文案示例

| ❌ 避免 | ✅ 推荐 |
|---------|---------|
| GIDO 后端未启动 | GIDO 后端未启动 |
| 发布到 GIDO | 提交到 GIDO / 发布到生产 |
| DataBatch 运维 | GIDO Batch 运维 |
| DS 项目 GIDO | DS 项目 **GIDO**（`DS_PROJECT_NAME` 默认） |
| 对齐阿里云 GIDO | 与 GIDO 发布治理一致 |

---

## 6. 修改检查清单

发版或改 UI 前自查：

1. `grep -ri dataworks gido/ --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=.venv` 无结果  
2. 后端 `HTTPException(detail=...)` / 告警 / 集成说明使用 `brand.py`  
3. `DS_PROJECT_NAME` 默认 **GIDO**  
4. `index.html` title 含「玑渡 GIDO」  
5. 新文档标题以 **玑渡 GIDO** 开头  

---

文档与排障手册可在首段注明：「本文中的 GIDO 指玑渡 GIDO 开源套件（原 gido 子项目）。」
