-- GIDO 外置 PostgreSQL 初始化权限（须用超级用户 / RDS master 执行）
--
-- 错误：permission denied for schema public (InsufficientPrivilege)
-- 原因：PostgreSQL 15+ 默认撤销 PUBLIC 在 schema public 上的 CREATE 权限
--
-- 1) 若库/用户尚未创建（按需执行，将 gido / 密码 替换为实际值）：
--    CREATE USER gido WITH PASSWORD 'your-password';
--    CREATE DATABASE gido OWNER gido ENCODING 'UTF8';
--
-- 2) 连接到目标库后执行下方 GRANT（将 gido 换成 INFRA_GIDO_DB_SERVICE_USER）：
--
--   psql -h YOUR_PG_HOST -U postgres -d gido -f k8s/postgres-external-init-grants.sql

GRANT CONNECT ON DATABASE gido TO gido;
GRANT USAGE, CREATE ON SCHEMA public TO gido;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gido;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gido;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gido;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO gido;

-- 可选（与 GRANT CREATE 二选一）：ALTER SCHEMA public OWNER TO gido;
