# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""平台权限码（resource:action），与菜单/能力对齐，便于前后端共用。"""

# 系统管理
SYSTEM_USER_READ = "system:user:read"
SYSTEM_USER_WRITE = "system:user:write"
SYSTEM_USER_DELETE = "system:user:delete"
SYSTEM_ROLE_READ = "system:role:read"
SYSTEM_ROLE_WRITE = "system:role:write"
SYSTEM_ROLE_DELETE = "system:role:delete"
SYSTEM_INTEGRATION_READ = "system:integration:read"
SYSTEM_INTEGRATION_WRITE = "system:integration:write"

# 工作空间（平台级：能否创建/管理成员等，仍配合工作空间成员角色做数据隔离）
WORKSPACE_READ = "workspace:read"
WORKSPACE_WRITE = "workspace:write"
WORKSPACE_MEMBER_MANAGE = "workspace:member:manage"

# GIDO Batch 各模块
GIDO_BATCH_STUDIO_READ = "gido:batch:studio:read"
GIDO_BATCH_STUDIO_WRITE = "gido:batch:studio:write"
GIDO_BATCH_STUDIO_RUN = "gido:batch:studio:run"

GIDO_BATCH_WORKFLOW_READ = "gido:batch:workflow:read"
GIDO_BATCH_WORKFLOW_WRITE = "gido:batch:workflow:write"
GIDO_BATCH_WORKFLOW_RUN = "gido:batch:workflow:run"

GIDO_BATCH_DATAMAP_READ = "gido:batch:datamap:read"
GIDO_BATCH_DATAMAP_WRITE = "gido:batch:datamap:write"

GIDO_BATCH_QUALITY_READ = "gido:batch:quality:read"
GIDO_BATCH_QUALITY_WRITE = "gido:batch:quality:write"

GIDO_BATCH_INTEGRATION_READ = "gido:batch:integration:read"
GIDO_BATCH_INTEGRATION_WRITE = "gido:batch:integration:write"
GIDO_BATCH_INTEGRATION_RUN = "gido:batch:integration:run"

GIDO_BATCH_OPERATION_READ = "gido:batch:operation:read"
GIDO_BATCH_OPERATION_WRITE = "gido:batch:operation:write"

GIDO_BATCH_DATASOURCE_READ = "gido:batch:datasource:read"
GIDO_BATCH_DATASOURCE_WRITE = "gido:batch:datasource:write"

# 临时只读 SQL（数据探查）；analyst 等只读角色可单独授予
GIDO_BATCH_PROBE_READ = "gido:batch:probe:read"

GIDO_SERVICE_READ = "gido:service:read"
GIDO_SERVICE_WRITE = "gido:service:write"
GIDO_SERVICE_RUN = "gido:service:run"

# GIDO Stream
GIDO_STREAM_READ = "gido:stream:read"
GIDO_STREAM_WRITE = "gido:stream:write"
GIDO_STREAM_RUN = "gido:stream:run"

AUDIT_READ = "audit:read"

ALL_PERMISSIONS = [
    SYSTEM_USER_READ, SYSTEM_USER_WRITE, SYSTEM_USER_DELETE,
    SYSTEM_ROLE_READ, SYSTEM_ROLE_WRITE, SYSTEM_ROLE_DELETE,
    SYSTEM_INTEGRATION_READ, SYSTEM_INTEGRATION_WRITE,
    WORKSPACE_READ, WORKSPACE_WRITE, WORKSPACE_MEMBER_MANAGE,
    GIDO_BATCH_STUDIO_READ, GIDO_BATCH_STUDIO_WRITE, GIDO_BATCH_STUDIO_RUN,
    GIDO_BATCH_WORKFLOW_READ, GIDO_BATCH_WORKFLOW_WRITE, GIDO_BATCH_WORKFLOW_RUN,
    GIDO_BATCH_DATAMAP_READ, GIDO_BATCH_DATAMAP_WRITE,
    GIDO_BATCH_QUALITY_READ, GIDO_BATCH_QUALITY_WRITE,
    GIDO_BATCH_INTEGRATION_READ, GIDO_BATCH_INTEGRATION_WRITE, GIDO_BATCH_INTEGRATION_RUN,
    GIDO_BATCH_OPERATION_READ, GIDO_BATCH_OPERATION_WRITE,
    GIDO_BATCH_DATASOURCE_READ, GIDO_BATCH_DATASOURCE_WRITE,
    GIDO_BATCH_PROBE_READ,
    GIDO_SERVICE_READ, GIDO_SERVICE_WRITE, GIDO_SERVICE_RUN,
    GIDO_STREAM_READ, GIDO_STREAM_WRITE, GIDO_STREAM_RUN,
    AUDIT_READ,
]

PERMISSION_LABELS = {
    SYSTEM_USER_READ: "系统-用户-查看",
    SYSTEM_USER_WRITE: "系统-用户-创建/修改",
    SYSTEM_USER_DELETE: "系统-用户-删除",
    SYSTEM_ROLE_READ: "系统-角色-查看",
    SYSTEM_ROLE_WRITE: "系统-角色-创建/修改",
    SYSTEM_ROLE_DELETE: "系统-角色-删除",
    SYSTEM_INTEGRATION_READ: "系统-集成-查看（Dolphin / Flink 等）",
    SYSTEM_INTEGRATION_WRITE: "系统-集成-配置（Dolphin / Flink 等）",
    WORKSPACE_READ: "工作空间-查看",
    WORKSPACE_WRITE: "工作空间-创建/修改",
    WORKSPACE_MEMBER_MANAGE: "工作空间-成员管理",
    GIDO_BATCH_STUDIO_READ: "数据开发-查看",
    GIDO_BATCH_STUDIO_WRITE: "数据开发-编辑",
    GIDO_BATCH_STUDIO_RUN: "数据开发-运行/发布",
    GIDO_BATCH_WORKFLOW_READ: "工作流-查看",
    GIDO_BATCH_WORKFLOW_WRITE: "工作流-编辑",
    GIDO_BATCH_WORKFLOW_RUN: "工作流-运行/发布",
    GIDO_BATCH_DATAMAP_READ: "数据字典-查看",
    GIDO_BATCH_DATAMAP_WRITE: "数据字典-编辑",
    GIDO_BATCH_QUALITY_READ: "数据质量-查看",
    GIDO_BATCH_QUALITY_WRITE: "数据质量-编辑",
    GIDO_BATCH_INTEGRATION_READ: "数据集成-查看",
    GIDO_BATCH_INTEGRATION_WRITE: "数据集成-编辑",
    GIDO_BATCH_INTEGRATION_RUN: "数据集成-运行",
    GIDO_BATCH_OPERATION_READ: "运维中心-查看",
    GIDO_BATCH_OPERATION_WRITE: "运维中心-操作",
    GIDO_BATCH_DATASOURCE_READ: "数据源-查看",
    GIDO_BATCH_DATASOURCE_WRITE: "数据源-编辑",
    GIDO_BATCH_PROBE_READ: "数据探查-临时只读查询",
    GIDO_SERVICE_READ: "数据服务-查看",
    GIDO_SERVICE_WRITE: "数据服务-编辑",
    GIDO_SERVICE_RUN: "数据服务-发布/调用",
    GIDO_STREAM_READ: "实时计算-查看",
    GIDO_STREAM_WRITE: "实时计算-编辑",
    GIDO_STREAM_RUN: "实时计算-提交/停止",
    AUDIT_READ: "审计日志-查看",
}

PERMISSION_MODULES = {
    SYSTEM_USER_READ: "系统管理",
    SYSTEM_ROLE_READ: "系统管理",
    SYSTEM_INTEGRATION_READ: "系统管理",
    SYSTEM_INTEGRATION_WRITE: "系统管理",
    WORKSPACE_READ: "工作空间",
    GIDO_BATCH_STUDIO_READ: "数据开发",
    GIDO_BATCH_WORKFLOW_READ: "工作流",
    GIDO_BATCH_DATAMAP_READ: "数据地图",
    GIDO_BATCH_QUALITY_READ: "数据质量",
    GIDO_BATCH_INTEGRATION_READ: "数据集成",
    GIDO_BATCH_OPERATION_READ: "运维中心",
    GIDO_BATCH_DATASOURCE_READ: "数据源",
    GIDO_BATCH_PROBE_READ: "数据探查",
    GIDO_SERVICE_READ: "数据服务",
    GIDO_STREAM_READ: "实时计算",
    AUDIT_READ: "审计",
}
