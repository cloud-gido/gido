# Flink 集成 — 运维交付工单模板（GIDO Stream开发）

> **用途**：将本页复制给运维/平台组，由对方填写「实际值」并完成网络与验收。GIDO 不内嵌 Flink，仅通过 HTTP 对接外部集群。

---

## 1. 背景（给运维的一句话）

GIDO **实时开发（Flink SQL / JAR）** 需要：

1. **JobManager REST** — 上传 JAR、提交/取消 JAR 作业、查询作业状态。  
2. **Flink SQL Gateway** — **所有 Flink SQL 作业** 必须通过 Gateway 的 REST（Open Session / Execute Statement），**不能**把 JobManager 的 Web 端口误当成 Gateway。  
3. **（可选）Flink Web UI** — 供用户在浏览器中打开作业拓扑；地址应对 **最终用户** 可达。

---

## 2. 请运维填写 — 环境与地址

| 配置项 | 环境变量名（后端 `.env`） | 填写实际值（示例留空） | 必填 |
|--------|---------------------------|------------------------|------|
| JobManager REST 根 URL | `FLINK_URL` | `http://________________:____` | **是** |
| SQL Gateway 根 URL | `FLINK_SQL_GATEWAY_URL` | `http://________________:____` | **是**（SQL 作业） |
| Gateway 提交目标 JM REST | `FLINK_GATEWAY_JOBMANAGER_REST_URL` | `http://________________:____` | 视部署而定 * |
| Flink Web UI（浏览器） | `FLINK_UI_URL` | `http://________________:____` | 建议 |

\* **何时必填**：Gateway 与 JobManager 不在同一网络、或 Gateway 容器内无法用默认方式解析 JM 时，必须提供 **Gateway 进程内可访问** 的 JM REST 地址（常为 K8s Service FQDN 或内网 VIP）。

**库内覆盖（可选）**：若使用 GIDO「系统管理 → Flink 集成」等界面配置，可与环境变量二选一或按产品说明合并优先级。

---

## 3. 部署形态勾选（便于排障）

- [ ] **Kubernetes（推荐）**：Flink Session 使用仓库根 `k8s/flink.yaml`；GIDO 在「系统管理 → 集成」填写 JM / Gateway REST（或环境变量 `FLINK_*`）。
  - 必须明确：**从 GIDO 后端 出站** 到 JM、Gateway 的 **Service / Ingress / port-forward** 是否放行。
  - `FLINK_GATEWAY_JOBMANAGER_REST_URL` 建议使用 **Gateway Pod 内可解析** 的 JM Service DNS（如 `http://flink-jobmanager.flink.svc.cluster.local:8081`）。
- [ ] **云上托管 Flink**（如全托管实时计算底层）  
  - 以云厂商文档为准，但必须同时暴露 **JM REST** 与 **SQL Gateway REST**（或等价网关），并把 **允许调用来源 IP / VPC** 列入白名单。

---

## 4. 网络与安全检查清单

| 检查项 | 要求 |
|--------|------|
| 出站：GIDO **backend** → `FLINK_URL` | 允许 TCP 访问 JM REST 端口 |
| 出站：GIDO **backend** → `FLINK_SQL_GATEWAY_URL` | 允许 TCP 访问 Gateway 端口 |
| 连通：SQL **Gateway 进程** → JobManager REST | Gateway 能向 JM 提交作业（与 `FLINK_GATEWAY_JOBMANAGER_REST_URL` 一致） |
| 入站/路由：用户浏览器 → `FLINK_UI_URL` | 用户点击「打开 Flink UI」可打开，**勿**仅集群内 DNS 且后端错误改写为仅容器可达地址 |
| TLS | 若使用 HTTPS，需有效证书或由运维提供信任链；后端 `curl` 需加 `-k` 仅用于临时排障 |

---

## 5. 验收命令（在 GIDO **backend 所在环境**执行）

将下面占位符换成实际上下文。

```bash
# 1) JobManager 是否可达（示例路径以集群为准，常见为 /overview 或根路径返回 JSON）
curl -sS -o /dev/null -w "%{http_code}\n" "${FLINK_URL}/overview"
# 期望：2xx（部分版本路径不同，可改为 curl -sS "${FLINK_URL}/" | head）

# 2) SQL Gateway 是否可达（具体健康路径依 Flink 版本，以下为常见探测）
curl -sS -o /dev/null -w "%{http_code}\n" "${FLINK_SQL_GATEWAY_URL}/v1/info"
# 若 404，请查阅当前 Flink 版本 SQL Gateway 文档中的 info/ready 接口

# 3)（可选）从 Gateway 所在 Pod/容器内访问 JM（与运维确认执行位置）
# curl -sS "${FLINK_GATEWAY_JOBMANAGER_REST_URL}/overview"
```

**平台侧验收（业务）**

1. GIDO Stream开发中创建 **Flink SQL** 作业，点击提交，**Flink UI 中出现对应 Job**。  
2. 在 **作业运维** 中状态与 Flink 一致；停止后状态能回落。  
3. **JAR 作业**：上传 JAR 后能提交；取消后作业结束。

---

## 6. 常见故障对照（给运维快速定位）

| 现象 | 可能原因 | 处理方向 |
|------|----------|----------|
| 后端日志提示「未配置 FLINK_SQL_GATEWAY_URL」 | 环境变量/库内未配 Gateway | 配置 `FLINK_SQL_GATEWAY_URL` |
| SQL 提交报 400 / Open Session 失败 | Gateway 与 JM 版本不匹配；或 `executionConfig` 不被接受 | 对齐 Flink/Gateway 版本；查看后端重试日志 |
| 后端能 curl JM，但 SQL 永远失败 | 未走 Gateway 或 Gateway 不通 | 单独验证 `FLINK_SQL_GATEWAY_URL` |
| 平台显示 running，Flink 已无作业 | 网络隔离或 JM 地址配错 | 核对 `FLINK_URL`、防火墙、多集群混用 |
| 用户点击 Flink UI 打不开 | `FLINK_UI_URL` 仅内网；或被错误替换为 docker 专用地址 | 提供用户可达的 UI 基地址 |
| Checkpoint / 状态后端报错 | 作业「参数调优」里配置了 HDFS/S3 等，集群无权限 | 运维配置存储与 IAM / Kerberos 等 |

---

## 7. 版本与容量（建议运维备注）

- **Flink 版本**：__________  
- **SQL Gateway 版本 / 镜像**：__________（建议与 Flink 主版本一致）  
- **默认并行度与 Slot**：由业务在作业内配置；运维侧保证 **足够 TaskManager / Slot**。

---

## 8. 联系人

| 角色 | 姓名 | 联系方式 |
|------|------|----------|
| GIDO 负责人 | | |
| Flink 集群负责人 | | |
| 网络/安全负责人 | | |

---

*文档随 GIDO Flink 插拔集成能力更新；若接口路径随 Flink 大版本变化，以官方 REST / SQL Gateway 文档为准。*
