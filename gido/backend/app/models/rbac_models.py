# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, Table, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base

role_permissions = Table(
    "dw_role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("dw_roles.id"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("dw_permissions.id"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "dw_permissions"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(128), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    module = Column(String(64), default="")
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")


class Role(Base):
    __tablename__ = "dw_roles"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text)
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles")
    users = relationship("User", back_populates="system_role")
