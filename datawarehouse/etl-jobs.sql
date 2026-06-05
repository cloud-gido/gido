-- ============================================
-- DolphinScheduler ETL 作业脚本
-- ============================================

-- ============================================
-- 作业1: MySQL → Doris ODS 层数据同步
-- ============================================

-- 同步用户数据
INSERT INTO ods_db.ods_user
SELECT 
    user_id,
    username,
    email,
    phone,
    gender,
    age,
    city,
    province,
    register_time,
    status,
    created_at,
    updated_at
FROM mysql_catalog.governance_db.dim_user
WHERE updated_at >= '${bizdate} 00:00:00' 
   OR created_at >= '${bizdate} 00:00:00';

-- 同步订单数据
INSERT INTO ods_db.ods_order_detail
SELECT 
    order_id,
    user_id,
    product_id,
    product_name,
    price,
    quantity,
    total_amount,
    pay_status,
    order_status,
    order_time,
    pay_time,
    province,
    city,
    created_at
FROM mysql_catalog.governance_db.dwd_order_detail
WHERE order_time >= '${bizdate} 00:00:00';

-- ============================================
-- 作业2: ODS → DWD 层数据加工
-- ============================================

INSERT INTO dwd_db.dwd_user_order_detail
SELECT 
    o.order_id,
    o.user_id,
    u.username,
    u.gender,
    u.age,
    o.city,
    o.province,
    o.product_id,
    o.product_name,
    o.price,
    o.quantity,
    o.total_amount,
    o.pay_status,
    o.order_status,
    o.order_time,
    o.pay_time,
    DATE(o.order_time) as order_date,
    HOUR(o.order_time) as order_hour
FROM ods_db.ods_order_detail o
LEFT JOIN ods_db.ods_user u ON o.user_id = u.user_id
WHERE DATE(o.order_time) = '${bizdate}';

-- ============================================
-- 作业3: DWD → DWS 层数据汇总
-- ============================================

-- 用户日统计
INSERT INTO dws_db.dws_user_daily_stats
SELECT 
    order_date as stat_date,
    user_id,
    username,
    COUNT(order_id) as order_count,
    SUM(total_amount) as order_amount,
    SUM(CASE WHEN pay_status = 1 THEN 1 ELSE 0 END) as pay_count,
    SUM(CASE WHEN pay_status = 1 THEN total_amount ELSE 0 END) as pay_amount,
    AVG(total_amount) as avg_order_amount
FROM dwd_db.dwd_user_order_detail
WHERE order_date = '${bizdate}'
GROUP BY order_date, user_id, username;

-- 城市日统计
INSERT INTO dws_db.dws_city_daily_stats
SELECT 
    order_date as stat_date,
    province,
    city,
    COUNT(DISTINCT user_id) as user_count,
    COUNT(order_id) as order_count,
    SUM(total_amount) as order_amount,
    AVG(total_amount) as avg_order_amount
FROM dwd_db.dwd_user_order_detail
WHERE order_date = '${bizdate}'
GROUP BY order_date, province, city;

-- ============================================
-- 作业4: DWS → ADS 层报表生成
-- ============================================

-- 日报表
INSERT INTO ads_db.ads_daily_report
SELECT 
    stat_date,
    (SELECT COUNT(DISTINCT user_id) FROM ods_db.ods_user WHERE DATE(created_at) <= stat_date) as total_users,
    (SELECT COUNT(DISTINCT user_id) FROM ods_db.ods_user WHERE DATE(created_at) = stat_date) as new_users,
    (SELECT COUNT(DISTINCT user_id) FROM dws_db.dws_user_daily_stats WHERE stat_date = s.stat_date) as active_users,
    SUM(order_count) as total_orders,
    SUM(order_amount) as total_amount,
    SUM(order_amount) / SUM(order_count) as avg_order_amount,
    SUM(pay_count) * 100.0 / SUM(order_count) as pay_rate
FROM dws_db.dws_user_daily_stats s
WHERE stat_date = '${bizdate}'
GROUP BY stat_date;

-- 城市排行 (TOP 10)
INSERT INTO ads_db.ads_city_rank
SELECT 
    stat_date,
    ROW_NUMBER() OVER (ORDER BY order_amount DESC) as rank_num,
    province,
    city,
    order_count,
    order_amount
FROM dws_db.dws_city_daily_stats
WHERE stat_date = '${bizdate}'
ORDER BY order_amount DESC
LIMIT 10;
