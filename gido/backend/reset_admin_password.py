# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

"""重置或创建 admin / admin123，并校验 bcrypt。在 backend 目录执行：
   .venv/bin/python reset_admin_password.py

会读取与后端相同的元数据库连接（DATABASE_URL 或 INFRA_GIDO_DB_* 组装结果），请先确认连的是你正在用的库。
"""
import sys
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, ".")

from app.models import rbac_models  # noqa: F401

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash, verify_password
from app.models.workspace import User

NEW_PASSWORD = "admin123"

def _mask_database_url(url: str) -> str:
    p = urlparse(url)
    if not p.password:
        return url
    userinfo = f"{p.username}:***" if p.username else "***"
    host = p.hostname or ""
    netloc = f"{userinfo}@{host}"
    if p.port:
        netloc = f"{netloc}:{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))

def main() -> None:
    print(f"元数据库连接（脱敏）: {_mask_database_url(settings.resolved_database_url)}")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        if not user:
            user = User(
                username="admin",
                email="admin@gido.com",
                full_name="管理员",
                hashed_password=get_password_hash(NEW_PASSWORD),
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print("已创建管理员账号 admin（数据库中原无该用户）。")
        else:
            user.hashed_password = get_password_hash(NEW_PASSWORD)
            user.is_active = True
            db.commit()
            db.refresh(user)
            print("已重置已有 admin 账号密码。")

        if not verify_password(NEW_PASSWORD, user.hashed_password):
            print("错误：写入后校验密码失败，请检查数据库连接与 dw_users.hashed_password 字段长度（建议 ≥256）。")
            sys.exit(1)
        print(f"校验通过。用户名: admin，密码: {NEW_PASSWORD}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
