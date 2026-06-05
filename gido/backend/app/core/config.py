# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

from app.core.infra_db_url import (
    build_postgres_sqlalchemy_url,
    infra_db_env_any_set,
    infra_db_env_complete,
    infra_db_env_partial_error_message,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "玑渡 GIDO"
    APP_VERSION: str = "1.0.0"

    # --- 元数据库（二选一）---
    # A) 本地/兼容：单一连接串（用户:密码 在 URL 内，不推荐生产）
    DATABASE_URL: str = "postgresql+psycopg2://root:DolphinPgDev%2172@127.0.0.1:5432/gido"
    # B) 生产/运维推荐：拆分变量（与 K8s Secret、INFRA_* 注入一致；四项齐全时优先于 DATABASE_URL）
    INFRA_GIDO_DB_SERVICE_URL: Optional[str] = None
    INFRA_GIDO_DB_SERVICE_USER: Optional[str] = None
    INFRA_GIDO_DB_SERVICE_PASSWORD: Optional[str] = None
    INFRA_GIDO_DB_SERVICE_READER: Optional[str] = Field(
        default=None,
        description="只读库账号用户名（可选；预留给只读副本/报表。当前 SQLAlchemy 引擎仍使用读写账号）。",
    )
    INFRA_GIDO_DB_URL: Optional[str] = Field(
        default=None,
        description="数据库名（如 gido）；勿与带账号密码的 JDBC 整串混淆。",
    )
    SECRET_KEY: str = "gido-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REDIS_URL: str = "redis://localhost:6379/0"

    # 告警配置
    ALERT_WEBHOOK_URL: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 25
    SMTP_FROM: str = "gido@example.com"
    ALERT_EMAIL: Optional[str] = None

    # DolphinScheduler 集成（须单独部署 DS；下列仅为「连到哪里」，不保证该地址已有服务）
    DS_ENABLED: bool = False
    DS_URL: str = "http://localhost:12345/dolphinscheduler"
    # Dolphin Web UI 根路径（留空则使用 DS_URL + "/ui"，用于工作流页跳转）
    DS_UI_URL: Optional[str] = None
    # 后端调 DS 仅用 HTTP Header「token」，与浏览器里账号密码登录无关；在外部 DS 上于安全中心创建 API Token 填入即可
    DS_TOKEN: str = ""
    # 预留字段，当前代码未使用；勿与 UI 登录混淆，对接外部 DS 请配置 DS_TOKEN
    DS_USERNAME: Optional[str] = None
    DS_PASSWORD: Optional[str] = None
    DS_PROJECT_NAME: str = "GIDO"
    # DS worker 回调 GIDO 时使用的内部 token
    INTERNAL_TOKEN: str = ""
    DEFAULT_TIMEZONE: str = "Asia/Shanghai"

    # 仅用于本地排障：启动时把 admin 密码设为该明文；用完后务必从 .env 删除
    RESET_ADMIN_PASSWORD: Optional[str] = None

    # Flink JobManager REST（overview / jobs / jars）；留空则仅依赖「系统管理 → 集成」库内配置或启动时注入
    FLINK_URL: Optional[str] = None
    # Flink SQL Gateway REST（/v1/sessions）；留空则仅依赖集成页或环境注入
    FLINK_SQL_GATEWAY_URL: Optional[str] = None
    # 实时作业「K8s Application」：Flink 作业镜像（SQL Gateway v4 deploy → kubernetes.container.image.ref）；默认同 k8s/flink.yaml
    FLINK_K8S_APPLICATION_IMAGE: str = "apache/flink:2.0.1-java11"
    # 可选：Application 部署后 GIDO 拉 jobId / 取消作业用的 JM REST 基址模板，须含 {cluster_id}（与 K8s Service 命名一致）
    # 例：http://{cluster_id}-rest.flink.svc.cluster.local:8081
    FLINK_K8S_APPLICATION_JM_REST_TEMPLATE: Optional[str] = None
    # 若设置且文件存在，Application 提交后可用 Kubernetes API 自动解析 ``{cluster_id}-rest`` 的 NodePort，
    # 拼接为 http://{FLINK_K8S_JM_NODEPORT_HOST}:{port} 轮询 jobId（容器内可将 kubeconfig 挂到 /root/.kube/host-kubeconfig，由 entrypoint 写入 /tmp/kube-for-backend）。
    FLINK_K8S_KUBECONFIG_PATH: Optional[str] = None
    # 与 NodePort 组合成 JM REST 基址；compose 内后端默认 host.docker.internal；本机 PyCharm 请改 127.0.0.1
    FLINK_K8S_JM_NODEPORT_HOST: str = "host.docker.internal"
    # K8s Application 默认 executionConfig（可被作业「参数调优」里顶级 k8s_application 覆盖）
    FLINK_K8S_NAMESPACE: Optional[str] = None
    # 集群 DNS 后缀（默认 cluster.local）；非标准域时与 JM/Gateway Service FQDN 一致
    FLINK_K8S_CLUSTER_DOMAIN: str = "cluster.local"
    # 可选：init 写 kubeconfig 时无 KUBERNETES_SERVICE_* 则使用该 apiserver URL（须可被 Pod 内解析）
    FLINK_K8S_APISERVER_FALLBACK_URL: Optional[str] = None
    # 可选：覆盖 -Djobmanager.rpc.address（留空则 flink-jobmanager.<namespace>.svc.<domain>）
    FLINK_K8S_JM_RPC_HOST: Optional[str] = None
    # 可选：覆盖 sql-gateway REST 广告地址（留空则 flink-sql-gateway.<namespace>.svc.<domain>）
    FLINK_K8S_SQL_GATEWAY_REST_HOST: Optional[str] = None
    FLINK_K8S_CONTEXT: Optional[str] = None
    FLINK_K8S_REST_EXPOSED_TYPE: Optional[str] = None
    # Gateway 执行 SQL 时要把作业提交到哪套 JM REST（hostname 必须能被 Gateway 进程解析；默认同 FLINK_URL）
    FLINK_GATEWAY_JOBMANAGER_REST_URL: Optional[str] = None
    # Flink Web UI（浏览器打开作业拓扑用）；不填则用 FLINK_URL（与 JM REST 同 host:port）
    FLINK_UI_URL: Optional[str] = None

    # 数据开发 publish / 实时作业提交 Flink 成功后是否自动锁定脚本与配置。生产建议 true（对齐 GIDO）；灰度/回滚可设 false
    STUDIO_LOCK_ON_PUBLISH: bool = True

    @property
    def resolved_database_url(self) -> str:
        """
        实际用于 SQLAlchemy 的连接串。
        若 INFRA_GIDO_DB_SERVICE_URL / _USER / _PASSWORD / INFRA_GIDO_DB_URL 四项齐全，
        则组装 postgresql+psycopg2://...（密码单独字段，便于 Secret 注入）；否则使用 DATABASE_URL。
        """
        if infra_db_env_complete(
            service_url=self.INFRA_GIDO_DB_SERVICE_URL,
            service_user=self.INFRA_GIDO_DB_SERVICE_USER,
            service_password=self.INFRA_GIDO_DB_SERVICE_PASSWORD,
            db_url=self.INFRA_GIDO_DB_URL,
        ):
            return build_postgres_sqlalchemy_url(
                service_url=self.INFRA_GIDO_DB_SERVICE_URL or "",
                user=(self.INFRA_GIDO_DB_SERVICE_USER or "").strip(),
                password=self.INFRA_GIDO_DB_SERVICE_PASSWORD,
                database_name=(self.INFRA_GIDO_DB_URL or "").strip(),
            )
        if infra_db_env_any_set(
            service_url=self.INFRA_GIDO_DB_SERVICE_URL,
            service_user=self.INFRA_GIDO_DB_SERVICE_USER,
            service_password=self.INFRA_GIDO_DB_SERVICE_PASSWORD,
            db_url=self.INFRA_GIDO_DB_URL,
            service_reader=self.INFRA_GIDO_DB_SERVICE_READER,
        ):
            raise ValueError(infra_db_env_partial_error_message())
        return self.DATABASE_URL


settings = Settings()
