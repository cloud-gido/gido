# K8s 遗留清单（可选，非生产推荐路径）

本目录存放 **历史参考** 与 **本地实验** 用清单，**新环境默认不部署**。

| 文件 | 说明 |
|------|------|
| `flink.yaml` | Flink Session + SQL Gateway（已弃用；生产请用 Flink Operator + `gido-flink-runtime`） |
| `flink-sql-gateway-*.yaml` | Session Gateway 对外暴露（Ingress / NodePort） |
| `redeploy-flink.sh` | 对齐 Session 集群镜像与 Deployment |
| `port-forward-jobmanager.sh` | 本机转发 JM 8081 |
| `dolphinscheduler.yaml` | DS 独立 K8s 示例（最小栈用 `k8s/gido.yaml` 内 `DS_ENABLED=false`） |
| `doris-fixed.yaml` | Doris 示例 |

**生产流作业**：`k8s/deploy-gido-k3s.sh` → `k8s/gido.yaml` + Operator RBAC + `k8s/build-flink-runtime.sh`。

若仍需 Session（如对照调试），在 Kind 栈可设 `GIDO_APPLY_FLINK=1`（见 `k8s/apply-gido-stack.sh`），会 apply 本目录 `flink.yaml`。
