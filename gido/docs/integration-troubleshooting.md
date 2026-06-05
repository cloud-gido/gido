# 玑渡 GIDO 集成与本地 Flink / Dolphin 排障汇总

> 品牌规范见 [BRAND.md](./BRAND.md)。下文 **GIDO** 指玑渡 GIDO 套件（代码目录仍为 `gido/`）。

本文汇总在 **GIDO Batch + GIDO Stream**、**DolphinScheduler（Docker）**、**Flink（Docker / SQL Gateway）** 联调过程中常见问题、根因与处理方式，便于后续自查与交接。

---

## 1. 前端：系统管理 / 平台集成 / 成员与权限

### 1.1 侧栏「成员与权限」与主区「平台集成」不一致

- **现象**：侧栏高亮「成员与权限」（路由仍为 `/gido/batch/admin`），主区却切到「平台集成」Tab，体验上像「成员页里怎么还有平台」。
- **原因**：路由未变，仅顶层 Tab 切换；平台集成另有侧栏入口 `/gido/batch/system/integration`。
- **处理**：成员与权限页已改为扁平 Tab（用户管理 / 角色与权限 / 工作空间成员），并去掉顶栏「平台集成」跳转按钮；集成统一走侧栏 **系统管理 → 平台集成**。

### 1.2 Flink 集成页信息架构

- **处理**：Flink 配置区改为左右两栏（左侧 Session/REST，右侧 Application/K8s），减少单页纵向滚动。

---

## 2. Docker Compose 与镜像策略

### 2.1 `docker compose up -d` 报 `unknown shorthand flag: 'd'`

- **常见原因**：命令实际跑在 **kubectl** 等非 docker 子命令上，或 `-f` 位置错误。
- **处理**：使用 `docker compose -f <文件> up --detach`；确认 `docker compose version` 可用。

### 2.2 不想拉镜像

- **处理**：`docker compose up -d --pull never`，或在 compose 里为服务设置 `pull_policy: never`。

---

## 3. DolphinScheduler（Docker，非 standalone）

### 3.1 `ds-schema` 退出码 **126**

- **原因**：`apache/dolphinscheduler-tools` 镜像 **ENTRYPOINT 已是 `bash`**。再写 `command: ["bash", "tools/bin/upgrade-schema.sh"]` 会变成执行 **`bash bash ...`**，导致 126。
- **处理**：与官方 `deploy/docker/docker-compose.yml` 一致，仅写  
  `command: ["tools/bin/upgrade-schema.sh"]`（平台栈已改为带重试的 `ds-schema-init.sh`）。

### 3.1b `ds-schema` / `platform-ds-schema` 退出码 **1**

- **现象**：`service "ds-schema" didn't complete successfully: exit 1`，Dolphin API/Master 起不来。
- **先看日志**：`docker logs platform-ds-schema`（或 `docker compose -f docker-compose-platform.yml logs ds-schema`）。
- **常见原因**：
  1. **PG 数据卷半初始化**（多次失败重试后表结构不完整）→ 开发环境重置卷后重起：
     ```bash
     ./start-platform.sh --reset-data
     # 或
     docker compose -f docker-compose-platform.yml down -v
     ./start-platform.sh
     ```
  2. **Docker 内存不足**，tools 容器内 `java` 无法启动 → 将 Docker Desktop 内存调到 **≥8GB**。
  3. **连接 PG 超时**（首轮 PG 刚 ready）→ 平台 compose 已内置 **5 次重试**；仍失败则查 `platform-postgres` 健康与密码 `POSTGRES_PASSWORD`。
  4. **`UnknownHostException: postgres`** → ds-schema 未在 Compose 网络内，或用了手动 `docker network create` 的外部网。请用最新 `./start-platform.sh`（平台网为 `bigdata-platform-network`），并 **勿** 单独 `docker run` schema 容器。JDBC 已改为 `platform-postgres`。
- **仅重跑 schema**（不删卷）：
  ```bash
  docker rm -f platform-ds-schema 2>/dev/null || true
  docker compose -f docker-compose-platform.yml up ds-schema --force-recreate
  ```

### 3.2 Master 容器 **ExitCode 137**（监控中心显示「Master节点不存在」）

- **含义**：进程被 **SIGKILL**，几乎都是 **内存不足（OOM）**——Docker 内存上限或整机 RAM 不够。
- **现象**：`dolphinscheduler-master` **Exited (137)**，UI「监控中心 → Master」为空；工作流无法调度（只有 API/Worker 在跑不够）。
- **处理**（按顺序）：
  1. **Docker Desktop → Settings → Resources**：内存调到 **≥ 8GB**（本机还跑 GIDO/Flink/Doris 时建议 **12GB+**）。
  2. 使用全栈编排 **`dockerFile/docker-compose.platform.yml`**（Master `-Xmx1024m`、`mem_limit: 1536m`）：
     ```bash
     ./start-platform.sh
     docker compose -f docker-compose-platform.yml ps
     docker logs platform-ds-master --tail 50
     ```
  3. 确认是否 OOM：`docker inspect platform-ds-master --format '{{.State.OOMKilled}}'`
  4. 仍 137：暂时停掉 Flink/Superset 等占内存服务后再起 Master，或把 `JAVA_TOOL_OPTIONS` 改为 `-Xmx768m` 后重建容器。

### 3.3 ZooKeeper 健康检查失败

- **原因**：`zookeeper:3.9` 镜像未必带 `nc`；用 `echo ruok | nc` 的健康检查易失败。
- **处理**：改用 `zkServer.sh status` 或 `bash` + `/dev/tcp` 等镜像内可用方式（见 `dockerFile/docker-compose.platform.yml`）。

### 3.4 GIDO「测试连接」报 **401 Unauthorized**（不是网络不通）

- **现象**：`401 Client Error: Unauthorized for url: http://host.docker.internal:12345/dolphinscheduler/projects?...`
- **说明**：能收到 401 说明 **Docker 里 Dolphin API 已启动且地址正确**；失败原因是 **Token 鉴权**，不是连不上。
- **常见原因**：
  1. 「DS API Token」留空保存 → **不会更新**库中旧 Token，旧令牌已对不上（例如重建过 Dolphin / 换过库）。
  2. Token 复制不完整、过期，或对应用户被禁用。
  3. API 根路径误填成带 `/ui` 的浏览器地址（应填 `http://host.docker.internal:12345/dolphinscheduler`）。
- **处理**：
  1. 浏览器打开 `http://127.0.0.1:12345/dolphinscheduler/ui`，登录 **admin / dolphinscheduler123**（默认，以你环境为准）。
  2. **安全中心 → 令牌管理 → 创建令牌**（用户选 admin），复制生成的 Token。
  3. GIDO「系统管理 → 平台集成 → Dolphin」：**DS API Token 必须粘贴完整新 Token 再保存**，然后点 **测试连接**。
  4. 容器内自测（与 GIDO 后端 同环境）：
     ```bash
     docker exec -it gido-backend sh -c \
       'curl -sS -o /dev/null -w "%{http_code}\n" -H "token: 你的Token" \
        "http://host.docker.internal:12345/dolphinscheduler/projects?pageNo=1&pageSize=1"'
     ```
     期望返回 **200**（或响应 JSON 里 `code:0`）。

### 3.4.1 令牌「失效时间早于创建时间」

- **现象**：UI 令牌列表里 **失效时间 ≤ 创建时间**（例如失效 17:24:52、创建 17:24:57），`curl` 始终 **401**、body 为空。
- **原因**：令牌在库中已视为 **过期**，与 HTTP 响应头 `Date` 使用 GMT **无关**。
- **处理**：删除该条令牌并 **新建**，失效时间设为明显晚于当前时间；完整排障步骤见 **[TROUBLESHOOTING_SOP.md](./TROUBLESHOOTING_SOP.md) §1**。

### 3.4.2 库中旧 Token 覆盖 `.env`

- **现象**：`.env` 已改 `GIDO_DS_TOKEN`，平台仍 401。
- **原因**：`ds_runtime` 规则为 **库中 `ds_token` 非空则覆盖环境变量**；集成页 **留空保存不会清库**。
- **处理**：集成或空间设置 **显式保存空 Token**（API 语义：`ds_token: ""` → 库置 `NULL`），或写入正确新 Token；**重启 backend**。

### 3.5 Worker 执行 SQL 任务报 `ClassNotFoundException: com.mysql.cj.jdbc.Driver`

- **原因**：ASF 镜像许可证策略，**MySQL JDBC 不在镜像 classpath**。
- **处理**：将 `mysql-connector-j-*.jar` 挂到 **`dolphinscheduler-api`** 与 **`dolphinscheduler-worker`** 的 `/opt/dolphinscheduler/libs/`（见 `dockerFile/docker-compose.platform.yml` 与 `dockerFile/jdbc/`）。

---

## 4. Flink（Docker Compose：Session + SQL Gateway）

### 4.1 官方镜像 `/opt/flink/lib` 无 Kafka SQL 连接器

- **现象**：`docker exec ... ls /opt/flink/lib | grep kafka` 为空；GIDO / SQL Client 里 `connector = 'kafka'` 的 INSERT 易失败或仅 Gateway 报 500。
- **原因**：**`apache/flink:2.0.1-java11` 不包含 `flink-sql-connector-kafka`**。
- **处理**：下载与 Flink 2.0 线匹配的 **`flink-sql-connector-kafka-4.0.1-2.0.jar`**（Maven Central），挂到 **jobmanager / taskmanager / sql-gateway** 的 `/opt/flink/lib/`（见 `dockerFile/docker-compose.platform.yml` 与 `dockerFile/flink-lib/`）。修改后需 **`./start-platform.sh --recreate`** 或 **`docker compose -f docker-compose-platform.yml up --force-recreate`**。

### 4.2 `bootstrap.servers` 与「本机能连」

- **说明**：Flink **TaskManager** 进程连 Kafka；**本机**或 **KafkaToolkitDemo** 能连 **≠** 容器内一定能连，但 Docker 默认 bridge 下，一般与 **宿主机到该 IP:端口** 一致。
- **自检**：`docker compose exec taskmanager bash -c 'timeout 4 bash -c "</dev/tcp/<host>/<port>" && echo OK || echo FAIL'`。

### 4.3 JobManager 日志里 `jobid` = `OK`

- **现象**：`Cannot resolve path parameter (jobid) from value "OK"`。
- **说明**：多为 **错误 REST 客户端**把字面量 `OK` 当成 jobId 请求 `/jobs/OK/...`，与业务作业本身无关；另见短生命周期作业已结束仍轮询旧 id 导致的 `Job ... not found`。

### 4.4 `platform-kafka`（综合平台 `docker-compose-platform.yml`）

综合平台栈使用 Confluent **`cp-kafka:7.5.0`**（KRaft 单节点），需同时满足：

| 场景 | Bootstrap / 连接地址 |
|------|----------------------|
| **Flink SQL**（platform 网内） | `kafka:29092` |
| **DataGovRN / kafka-tool / 局域网** | `${KAFKA_LAN_HOST}:9092`（如 `192.168.1.68:9092`） |

编排文件：`dockerFile/docker-compose.platform.yml`；一键脚本：`./start-platform.sh`（会自动检测 `KAFKA_LAN_HOST`）。

#### 4.4.1 容器不在运行（`docker ps` 无 `platform-kafka`）

- **现象**：Flink 连 Kafka 失败；平台栈其它服务正常但无 Kafka。
- **常见原因**：
  1. 首次启动失败后 **无 `restart` 策略**，容器退出后一直停着（compose 已加 `restart: unless-stopped`）。
  2. 数据目录权限错误导致启动即崩溃（见 §4.4.2）。
- **处理**：
  ```bash
  docker ps -a | grep kafka
  docker logs platform-kafka --tail 80
  docker compose -f docker-compose-platform.yml up -d kafka
  ```

#### 4.4.2 `Permission denied`（`/tmp/kraft-combined-logs/meta.properties.tmp`）

- **现象**：
  ```
  FileNotFoundException: /tmp/kraft-combined-logs/meta.properties.tmp (Permission denied)
  ```
- **原因**：`cp-kafka` 以 **`appuser`(uid 1000)** 运行；数据卷若挂在 **`/tmp/kraft-combined-logs`**，对该用户 **不可写**。
- **处理**（compose 已修正）：
  ```yaml
  KAFKA_LOG_DIRS: /var/lib/kafka/data
  volumes:
    - kafka-data:/var/lib/kafka/data
  ```
- **若旧卷已损坏**，删卷重建：
  ```bash
  docker compose -f docker-compose-platform.yml stop kafka
  docker rm -f platform-kafka
  docker volume ls | grep kafka-data
  docker volume rm <卷名>
  docker compose -f docker-compose-platform.yml up -d kafka
  ```

#### 4.4.3 客户端连不上 / DataGovRN 一直「连接中…」（advertised 与 bootstrap 不一致）

- **现象**：`platform-kafka` 为 **healthy**，宿主机 `9092` 已映射；DataGovRN（或其它 Docker 容器）填 `192.168.1.68:9092` 仍连不上。
- **原因（Kafka 经典坑）**：客户端连接分两步——
  1. **Bootstrap**：先连你填的地址；
  2. **Metadata**：Broker 返回 **advertised 地址**，客户端再按该地址连。
  
  若曾配置 `PLAINTEXT_HOST://localhost:9092` 或 `host.docker.internal:9092`，而客户端填 `192.168.1.68:9092`，metadata 返回的地址与 bootstrap 不一致，**Docker 容器内** 解析 `localhost` 会指向容器自身 → 失败。

| 客户端 bootstrap | Broker advertised | 结果 |
|------------------|-------------------|------|
| `192.168.1.68:9092` | `localhost:9092` | datagovrn 容器内 localhost 无 Kafka → **失败** |
| `192.168.1.68:9092` | `host.docker.internal:9092` | 与 bootstrap 不一致 → 易卡住 |

- **处理（双 Listener，当前方案）**：

  | Listener | 监听 | Advertised | 谁用 |
  |----------|------|------------|------|
  | **PLAINTEXT** | `kafka:29092` | `kafka:29092` | platform 网内 Flink |
  | **PLAINTEXT_HOST** | `0.0.0.0:9092` | `${KAFKA_LAN_HOST}:9092` | 局域网、DataGovRN、本机工具 |

  ```yaml
  KAFKA_LISTENERS: PLAINTEXT://kafka:29092,...,PLAINTEXT_HOST://0.0.0.0:9092
  KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://${KAFKA_LAN_HOST}:9092
  ports:
    - "9092:9092"
  ```

- **`KAFKA_LAN_HOST` 来源**（优先级从高到低）：
  1. 根目录 `.env` 中手动配置：`KAFKA_LAN_HOST=192.168.1.68`
  2. `./start-platform.sh` 自动检测 `en0/en1` 局域网 IP
  3. 检测失败则 fallback 为 `host.docker.internal`

- **各场景填法**：
  - Flink SQL：`'bootstrap.servers' = 'kafka:29092'`（勿写宿主机 IP）
  - DataGovRN / kafka-tool：`192.168.1.68:9092`（**须与 `KAFKA_LAN_HOST` 一致**）
  - **勿填** `localhost:9092`（在 Docker 容器里指容器自身，不是宿主机 Kafka）

- **改 advertised 后必须重建**：
  ```bash
  docker compose -f docker-compose-platform.yml up -d kafka --force-recreate
  docker inspect platform-kafka --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ADVERTISED
  ```

#### 4.4.4 运维命令速查

```bash
# 启动 / 停止平台栈
./start-platform.sh
./start-platform.sh stop
./start-platform.sh down

# 仅重建 Kafka
docker compose -f docker-compose-platform.yml up -d kafka --force-recreate

# 容器内自测
docker exec platform-kafka kafka-topics --bootstrap-server localhost:9092 --list

# 从 DataGovRN 测连通（替换为实际 LAN IP）
docker exec datagovrn nc -zv 192.168.1.68 9092
```

#### 4.4.5 经验小结

1. **cp-kafka 数据目录**用 `/var/lib/kafka/data`，不要用 `/tmp/kraft-combined-logs`。
2. **advertised 地址**决定客户端最终连哪里，不是 bootstrap 填什么就连什么。
3. **Docker 容器访问 Kafka**不要用 `localhost`；用 **宿主机 LAN IP** 或确保 advertised 与 bootstrap 一致。
4. **Flink 走内网** `kafka:29092`，与对外 `9092` 分开，互不干扰。
5. 平台网络由 Compose 管理（`bigdata-platform-network`）；**勿**手动 `docker network create` 后指望服务名 `postgres`/`kafka` 能解析（见 §3.1b）。

---

## 5. Flink SQL（GIDO Stream / SQL Gateway）

### 5.1 SELECT 引用未声明列（如 `msg_key`）

- **原因**：源表 `CREATE TABLE` 只有 `seq, ts`，却写 `SELECT msg_key, ...`。
- **处理**：要么 **去掉 `msg_key`**，只 `SELECT seq, ts`；要么在源表增加  
  `msg_key STRING METADATA FROM 'key' VIRTUAL` 且 sink 列对齐。

### 5.2 `datagen → print` 能跑，`kafka → print` 不行

- **排查顺序**：① TM 到 Kafka 网络；② **Kafka SQL JAR**（见 4.1）；③ `scan.startup.mode`（`latest-offset` 易「无新数据」表象，调试用 `earliest-offset`）；④ topic 是否存在。

### 5.3 Gateway `fetchResults` **HTTP 500** + `SqlGatewayException`

- **现象**：平台提示「第 n 条语句失败」且正文是一大段 Gateway 栈。
- **原因**：Flink 2.x 对流式 INSERT，Gateway 常 **ERROR + fetchResults 500**；真实原因多在 **JobManager 作业异常**。
- **处理（平台侧）**：已在 `gido/backend/app/api/streaming.py` 增加逻辑：Gateway **ERROR** 时从 JM **差分新 job**，对 **FAILED/CANCELED/FAILING** 拉 **`/jobs/{id}/exceptions`** 拼进报错。部署后端后重试提交，应能看到 **`Caused by`** 级根因。

### 5.4 多语句与分号

- **说明**：后端按 **`;`** 拆句依次提交 SQL Gateway；注释与空段会被跳过。

---

## 6. Cursor / 本地规则（若出现）

### 6.1 `yaml.parse` / `args must be a string`

- **说明**：属于 **Cursor 规则/自动化** 配置校验问题，与业务 YAML 语法无关。
- **处理**：规则里不要用不支持的 action；`args` 须为字符串；或 CLI 使用官方支持的 compose 命令。

---

## 7. 相关文件索引（仓库内）

| 主题 | 路径 |
|------|------|
| **按现象排障 SOP（含 2026-05-20 案例）** | **`docs/TROUBLESHOOTING_SOP.md`** |
| Dolphin / Flink / Kafka 全栈 Compose | **`docker-compose-platform.yml`**、`dockerFile/docker-compose.platform.yml` |
| Dolphin JDBC 驱动目录 | `dockerFile/jdbc/`（`mysql-connector-j-8.0.33.jar`，`.gitignore` 忽略 jar） |
| **平台一键脚本** | **`start-platform.sh`**（根目录） |
| Flink Kafka 连接器 jar | `dockerFile/flink-lib/flink-sql-connector-kafka-4.0.1-2.0.jar` |
| Flink K8s 参考清单 | `k8s/flink.yaml` |
| Dolphin K8s 参考清单 | `k8s/dolphinscheduler.yaml` |
| SQL Gateway 提交与回落逻辑 | `gido/backend/app/api/streaming.py`（`submit_sql`、`_observe_new_job_for_gateway_error`） |
| 成员与权限 / 集成 UI | `gido/frontend/src/pages/SystemRbac.tsx`、`MainLayout.tsx`、`routes.ts`、`App.tsx` |

---

## 8. 建议的最小验证路径

1. **Dolphin + Flink + Kafka（推荐）**：`./start-platform.sh`，Dolphin UI `http://localhost:12345/dolphinscheduler/ui`，GIDO 测连接。  
2. **Flink Kafka 连接器**：确认 `flink-lib` 下 jar 存在 → `./start-platform.sh` → `docker exec platform-flink-jm ls /opt/flink/lib | grep kafka`。  
3. **平台 Kafka**：`./start-platform.sh` 或 `docker compose -f docker-compose-platform.yml up -d kafka` → `docker inspect platform-kafka | grep ADVERTISED` → DataGovRN 填 `${KAFKA_LAN_HOST}:9092`。  
4. **Flink SQL**：`datagen+print` 一条链；再 `kafka+print`（`bootstrap.servers=kafka:29092`，`earliest-offset`）；最后再接业务 sink。

---

文档随排障经验可继续追加条目；修改 compose 或后端回落逻辑时请同步更新 **§3 / §4 / §5** 与 **§7 索引**（平台 Kafka 见 **§4.4**）。
