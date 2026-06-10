# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据服务：API 定义、消费者应用、授权与调用日志。"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class DataApi(Base):
    __tablename__ = "dw_data_apis"
    __table_args__ = (UniqueConstraint("workspace_id", "api_code", name="uq_dw_data_api_ws_code"),)

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    api_code = Column(String(64), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text)
    mode = Column(String(16), nullable=False, default="sql")  # sql | wizard | http
    http_method = Column(String(8), default="GET")  # GET | POST
    status = Column(String(16), nullable=False, default="draft")  # draft | online | offline
    version = Column(Integer, default=1)
    datasource_id = Column(Integer, ForeignKey("dw_datasources.id"), nullable=True)
    sql_template = Column(Text)
    wizard_config = Column(JSON)  # table, fields, filters
    response_fields = Column(JSON)  # [{name, alias, mask_type}]
    pagination_enabled = Column(Boolean, default=True)
    page_size_default = Column(Integer, default=20)
    page_size_max = Column(Integer, default=1000)
    timeout_seconds = Column(Integer, default=30)
    cache_ttl_seconds = Column(Integer, default=0)  # 0=不缓存
    max_rows = Column(Integer, default=10000)
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    published_at = Column(DateTime, nullable=True)
    published_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)

    params = relationship(
        "DataApiParam",
        back_populates="api",
        cascade="all, delete-orphan",
    )


class DataApiParam(Base):
    __tablename__ = "dw_data_api_params"

    id = Column(Integer, primary_key=True, index=True)
    api_id = Column(Integer, ForeignKey("dw_data_apis.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    param_in = Column(String(16), default="query")  # query | body | path
    data_type = Column(String(16), default="string")  # string|int|long|float|bool|date|datetime
    required = Column(Boolean, default=False)
    default_value = Column(String(512))
    description = Column(String(256))
    validator_regex = Column(String(256))
    sort_order = Column(Integer, default=0)

    api = relationship("DataApi", back_populates="params")


class ConsumerApp(Base):
    __tablename__ = "dw_consumer_apps"
    __table_args__ = (UniqueConstraint("workspace_id", "app_key", name="uq_dw_consumer_app_ws_key"),)

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text)
    app_key = Column(String(32), nullable=False)
    app_secret_hash = Column(String(256), nullable=False)
    ip_whitelist = Column(JSON)  # ["1.2.3.4", "10.0.0.0/8"]
    qps_limit = Column(Integer, default=100)
    daily_quota = Column(Integer, default=100000)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)

    grants = relationship("ConsumerAppApiGrant", back_populates="app", cascade="all, delete-orphan")


class ConsumerAppApiGrant(Base):
    __tablename__ = "dw_consumer_app_api_grants"
    __table_args__ = (UniqueConstraint("app_id", "api_id", name="uq_dw_app_api_grant"),)

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(Integer, ForeignKey("dw_consumer_apps.id", ondelete="CASCADE"), nullable=False)
    api_id = Column(Integer, ForeignKey("dw_data_apis.id", ondelete="CASCADE"), nullable=False)
    qps_limit = Column(Integer, nullable=True)  # 覆盖 app 级
    created_at = Column(DateTime, default=datetime.utcnow)

    app = relationship("ConsumerApp", back_populates="grants")


class DataApiInvocationLog(Base):
    __tablename__ = "dw_data_api_invocation_logs"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), index=True)
    api_id = Column(Integer, ForeignKey("dw_data_apis.id"), index=True)
    app_id = Column(Integer, ForeignKey("dw_consumer_apps.id"), nullable=True)
    trace_id = Column(String(64), index=True)
    http_method = Column(String(8))
    client_ip = Column(String(64))
    request_params = Column(JSON)
    status_code = Column(Integer)
    row_count = Column(Integer, default=0)
    latency_ms = Column(Float)
    cache_hit = Column(Boolean, default=False)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
