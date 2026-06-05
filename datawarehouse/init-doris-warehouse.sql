-- ============================================
-- Doris 数仓初始化脚本
-- ============================================

-- 创建 ODS 层（原始数据层）
CREATE DATABASE IF NOT EXISTS ods_db;
CREATE DATABASE IF NOT EXISTS dwd_db;
CREATE DATABASE IF NOT EXISTS dws_db;
CREATE DATABASE IF NOT EXISTS ads_db;

-- ============================================
-- ODS 层 - 原始数据表
-- ============================================
USE ods_db;

-- 用户维度表（来自 MySQL dim_user）
CREATE TABLE IF NOT EXISTS ods_user (
    user_id BIGINT COMMENT '用户ID',
    username VARCHAR(100) COMMENT '用户名',
    email VARCHAR(200) COMMENT '邮箱',
    phone VARCHAR(20) COMMENT '手机号',
    gender TINYINT COMMENT '性别',
    age INT COMMENT '年龄',
    city VARCHAR(100) COMMENT '城市',
    province VARCHAR(100) COMMENT '省份',
    register_time DATETIME COMMENT '注册时间',
    status TINYINT COMMENT '状态',
    created_at DATETIME COMMENT '创建时间',
    updated_at DATETIME COMMENT '更新时间'
) ENGINE=OLAP
DUPLICATE KEY(user_id)
COMMENT '用户维度表-ODS层'
DISTRIBUTED BY HASH(user_id) BUCKETS 10
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- 订单明细表（来自 MySQL dwd_order_detail）
CREATE TABLE IF NOT EXISTS ods_order_detail (
    order_id BIGINT COMMENT '订单ID',
    user_id BIGINT COMMENT '用户ID',
    product_id BIGINT COMMENT '商品ID',
    product_name VARCHAR(200) COMMENT '商品名称',
    price DECIMAL(10, 2) COMMENT '单价',
    quantity INT COMMENT '数量',
    total_amount DECIMAL(10, 2) COMMENT '总金额',
    pay_status TINYINT COMMENT '支付状态',
    order_status TINYINT COMMENT '订单状态',
    order_time DATETIME COMMENT '下单时间',
    pay_time DATETIME COMMENT '支付时间',
    province VARCHAR(100) COMMENT '省份',
    city VARCHAR(100) COMMENT '城市',
    created_at DATETIME COMMENT '创建时间'
) ENGINE=OLAP
DUPLICATE KEY(order_id)
COMMENT '订单明细表-ODS层'
DISTRIBUTED BY HASH(order_id) BUCKETS 10
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- ============================================
-- DWD 层 - 明细数据层
-- ============================================
USE dwd_db;

-- 用户订单宽表
CREATE TABLE IF NOT EXISTS dwd_user_order_detail (
    order_id BIGINT COMMENT '订单ID',
    user_id BIGINT COMMENT '用户ID',
    username VARCHAR(100) COMMENT '用户名',
    gender TINYINT COMMENT '性别',
    age INT COMMENT '年龄',
    city VARCHAR(100) COMMENT '城市',
    province VARCHAR(100) COMMENT '省份',
    product_id BIGINT COMMENT '商品ID',
    product_name VARCHAR(200) COMMENT '商品名称',
    price DECIMAL(10, 2) COMMENT '单价',
    quantity INT COMMENT '数量',
    total_amount DECIMAL(10, 2) COMMENT '总金额',
    pay_status TINYINT COMMENT '支付状态',
    order_status TINYINT COMMENT '订单状态',
    order_time DATETIME COMMENT '下单时间',
    pay_time DATETIME COMMENT '支付时间',
    order_date DATE COMMENT '订单日期',
    order_hour INT COMMENT '订单小时'
) ENGINE=OLAP
DUPLICATE KEY(order_id)
COMMENT '用户订单宽表-DWD层'
DISTRIBUTED BY HASH(order_id) BUCKETS 10
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- ============================================
-- DWS 层 - 汇总数据层
-- ============================================
USE dws_db;

-- 用户日统计
CREATE TABLE dws_user_daily_stats (
    stat_date DATE COMMENT '统计日期',
    user_id BIGINT COMMENT '用户ID',
    username VARCHAR(100) COMMENT '用户名',
    order_count BIGINT COMMENT '订单数',
    order_amount DECIMAL(12, 2) COMMENT '订单金额',
    pay_count BIGINT COMMENT '支付次数',
    pay_amount DECIMAL(12, 2) COMMENT '支付金额',
    avg_order_amount DECIMAL(10, 2) COMMENT '平均订单金额'
) ENGINE=OLAP
UNIQUE KEY(stat_date, user_id)
COMMENT '用户日统计-DWS层'
DISTRIBUTED BY HASH(user_id) BUCKETS 10
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- 城市日统计
CREATE TABLE dws_city_daily_stats (
    stat_date DATE COMMENT '统计日期',
    province VARCHAR(100) COMMENT '省份',
    city VARCHAR(100) COMMENT '城市',
    user_count BIGINT COMMENT '用户数',
    order_count BIGINT COMMENT '订单数',
    order_amount DECIMAL(12, 2) COMMENT '订单金额',
    avg_order_amount DECIMAL(10, 2) COMMENT '平均订单金额'
) ENGINE=OLAP
UNIQUE KEY(stat_date, province, city)
COMMENT '城市日统计-DWS层'
DISTRIBUTED BY HASH(stat_date) BUCKETS 10
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- ============================================
-- ADS 层 - 应用数据层
-- ============================================
USE ads_db;

-- 日报表
CREATE TABLE ads_daily_report (
    stat_date DATE COMMENT '统计日期',
    total_users BIGINT COMMENT '总用户数',
    new_users BIGINT COMMENT '新增用户数',
    active_users BIGINT COMMENT '活跃用户数',
    total_orders BIGINT COMMENT '总订单数',
    total_amount DECIMAL(12, 2) COMMENT '总金额',
    avg_order_amount DECIMAL(10, 2) COMMENT '平均订单金额',
    pay_rate DECIMAL(5, 2) COMMENT '支付率'
) ENGINE=OLAP
UNIQUE KEY(stat_date)
COMMENT '日报表-ADS层'
DISTRIBUTED BY HASH(stat_date) BUCKETS 5
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- 城市排行
CREATE TABLE ads_city_rank (
    stat_date DATE COMMENT '统计日期',
    rank_num INT COMMENT '排名',
    province VARCHAR(100) COMMENT '省份',
    city VARCHAR(100) COMMENT '城市',
    order_count BIGINT COMMENT '订单数',
    order_amount DECIMAL(12, 2) COMMENT '订单金额'
) ENGINE=OLAP
UNIQUE KEY(stat_date, rank_num)
COMMENT '城市排行-ADS层'
DISTRIBUTED BY HASH(stat_date) BUCKETS 5
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1"
);

-- ============================================
-- 创建用户和授权
-- ============================================
CREATE USER IF NOT EXISTS 'dw_user'@'%' IDENTIFIED BY 'dw_password123';
GRANT SELECT, INSERT, UPDATE ON ods_db.* TO 'dw_user'@'%';
GRANT SELECT, INSERT, UPDATE ON dwd_db.* TO 'dw_user'@'%';
GRANT SELECT, INSERT, UPDATE ON dws_db.* TO 'dw_user'@'%';
GRANT SELECT, INSERT, UPDATE ON ads_db.* TO 'dw_user'@'%';
FLUSH PRIVILEGES;
