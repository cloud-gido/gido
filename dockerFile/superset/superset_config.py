# GIDO 本地 / Docker：Superset 元数据库与缓存（复用已有 PG / Redis）

import os
from urllib.parse import quote_plus


def _redis_url(db: int) -> str:
    host = os.environ.get("REDIS_HOST", "redis")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "").strip()
    if password:
        return f"redis://:{quote_plus(password)}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "gido_dev_superset_secret_change_me")

SQLALCHEMY_DATABASE_URI = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://root:DolphinPgDev%2172@postgres:5432/superset",
)

_broker_db = int(os.environ.get("REDIS_DB_CELERY_BROKER", "0"))
_result_db = int(os.environ.get("REDIS_DB_CELERY_RESULT", "1"))
_cache_db = int(os.environ.get("REDIS_DB_CACHE", "2"))


class CeleryConfig:
    broker_url = _redis_url(_broker_db)
    result_backend = _redis_url(_result_db)
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    )
    worker_prefetch_multiplier = 1
    task_acks_late = False


CELERY_CONFIG = CeleryConfig

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_URL": _redis_url(_cache_db),
}
DATA_CACHE_CONFIG = CACHE_CONFIG

WTF_CSRF_ENABLED = True
TALISMAN_ENABLED = os.environ.get("SUPERSET_TALISMAN_ENABLED", "false").lower() in ("1", "true", "yes")

FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
}

# 界面语言（官方镜像内置翻译，无单独「汉化版」）
# 默认简体中文；可在 .env 设 SUPERSET_LOCALE=en 改回英文
BABEL_DEFAULT_LOCALE = os.environ.get("SUPERSET_LOCALE", "zh")

LANGUAGES = {
    "en": {"flag": "us", "name": "English"},
    "zh": {"flag": "cn", "name": "简体中文"},
}
