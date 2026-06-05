-- ============================================
-- 游戏/博彩数仓表结构初始化脚本
-- ============================================

USE bigdata_dw;

-- 1. 玩家维度表
CREATE TABLE IF NOT EXISTS dim_player (
    player_id BIGINT PRIMARY KEY COMMENT '玩家ID',
    username VARCHAR(100) COMMENT '用户名',
    nickname VARCHAR(100) COMMENT '昵称',
    email VARCHAR(200) COMMENT '邮箱',
    phone VARCHAR(20) COMMENT '手机号',
    gender TINYINT COMMENT '性别: 0-未知, 1-男, 2-女',
    age INT COMMENT '年龄',
    country VARCHAR(100) COMMENT '国家',
    province VARCHAR(100) COMMENT '省份',
    city VARCHAR(100) COMMENT '城市',
    register_time DATETIME COMMENT '注册时间',
    last_login_time DATETIME COMMENT '最后登录时间',
    vip_level INT DEFAULT 0 COMMENT 'VIP等级',
    total_recharge DECIMAL(12, 2) DEFAULT 0.00 COMMENT '累计充值金额',
    status TINYINT DEFAULT 1 COMMENT '状态: 0-禁用, 1-正常',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_register_time (register_time),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='玩家维度表';

-- 2. 投注订单明细表（核心事实表）
CREATE TABLE IF NOT EXISTS dwd_bet_order_detail (
    order_id BIGINT PRIMARY KEY COMMENT '订单ID',
    player_id BIGINT COMMENT '玩家ID',
    username VARCHAR(100) COMMENT '用户名',
    game_id INT COMMENT '游戏ID',
    game_name VARCHAR(200) COMMENT '游戏名称',
    bet_type VARCHAR(50) COMMENT '投注类型: lottery-彩票, sports-体育, casino-赌场',
    bet_amount DECIMAL(10, 2) COMMENT '投注金额',
    win_amount DECIMAL(10, 2) DEFAULT 0.00 COMMENT '赢得金额',
    profit_loss DECIMAL(10, 2) DEFAULT 0.00 COMMENT '盈亏金额',
    odds DECIMAL(6, 2) COMMENT '赔率',
    bet_status TINYINT COMMENT '投注状态: 0-待开奖, 1-已中奖, 2-未中奖, 3-已取消, 4-退款',
    order_status TINYINT COMMENT '订单状态: 0-待支付, 1-已支付, 2-已完成, 3-已取消',
    bet_time DATETIME COMMENT '投注时间',
    settle_time DATETIME COMMENT '结算时间',
    ip_address VARCHAR(50) COMMENT 'IP地址',
    device_type VARCHAR(50) COMMENT '设备类型: web, ios, android',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_player_id (player_id),
    INDEX idx_bet_time (bet_time),
    INDEX idx_game_id (game_id),
    INDEX idx_bet_status (bet_status),
    INDEX idx_order_status (order_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投注订单明细事实表';

-- 3. 充值订单表
CREATE TABLE IF NOT EXISTS dwd_recharge_order (
    order_id BIGINT PRIMARY KEY COMMENT '订单ID',
    player_id BIGINT COMMENT '玩家ID',
    username VARCHAR(100) COMMENT '用户名',
    recharge_amount DECIMAL(10, 2) COMMENT '充值金额',
    actual_amount DECIMAL(10, 2) COMMENT '实际到账金额',
    bonus_amount DECIMAL(10, 2) DEFAULT 0.00 COMMENT '赠送金额',
    payment_method VARCHAR(50) COMMENT '支付方式: alipay, wechat, bank, crypto',
    currency VARCHAR(10) DEFAULT 'CNY' COMMENT '货币类型',
    pay_status TINYINT COMMENT '支付状态: 0-待支付, 1-已支付, 2-失败, 3-退款',
    order_time DATETIME COMMENT '下单时间',
    pay_time DATETIME COMMENT '支付时间',
    ip_address VARCHAR(50) COMMENT 'IP地址',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_player_id (player_id),
    INDEX idx_order_time (order_time),
    INDEX idx_pay_status (pay_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='充值订单表';

-- 4. 玩家日统计表（DWS层）
CREATE TABLE IF NOT EXISTS dws_player_daily_stats (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stat_date DATE COMMENT '统计日期',
    player_id BIGINT COMMENT '玩家ID',
    username VARCHAR(100) COMMENT '用户名',
    login_count INT DEFAULT 0 COMMENT '登录次数',
    bet_count INT DEFAULT 0 COMMENT '投注次数',
    bet_amount DECIMAL(12, 2) DEFAULT 0.00 COMMENT '投注金额',
    win_amount DECIMAL(12, 2) DEFAULT 0.00 COMMENT '赢得金额',
    profit_loss DECIMAL(12, 2) DEFAULT 0.00 COMMENT '盈亏金额',
    recharge_count INT DEFAULT 0 COMMENT '充值次数',
    recharge_amount DECIMAL(12, 2) DEFAULT 0.00 COMMENT '充值金额',
    withdraw_count INT DEFAULT 0 COMMENT '提现次数',
    withdraw_amount DECIMAL(12, 2) DEFAULT 0.00 COMMENT '提现金额',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_date_player (stat_date, player_id),
    INDEX idx_stat_date (stat_date),
    INDEX idx_player_id (player_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='玩家日统计表';

-- 5. 每日汇总报表（ADS层）
CREATE TABLE IF NOT EXISTS ads_daily_report (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stat_date DATE COMMENT '统计日期',
    total_players INT DEFAULT 0 COMMENT '总玩家数',
    new_players INT DEFAULT 0 COMMENT '新增玩家数',
    active_players INT DEFAULT 0 COMMENT '活跃玩家数',
    total_bets INT DEFAULT 0 COMMENT '总投注次数',
    total_bet_amount DECIMAL(14, 2) DEFAULT 0.00 COMMENT '总投注金额',
    total_win_amount DECIMAL(14, 2) DEFAULT 0.00 COMMENT '总赢得金额',
    total_profit DECIMAL(14, 2) DEFAULT 0.00 COMMENT '平台总盈利',
    total_recharge DECIMAL(14, 2) DEFAULT 0.00 COMMENT '总充值金额',
    total_withdraw DECIMAL(14, 2) DEFAULT 0.00 COMMENT '总提现金额',
    avg_bet_amount DECIMAL(10, 2) DEFAULT 0.00 COMMENT '平均投注金额',
    conversion_rate DECIMAL(5, 4) DEFAULT 0.0000 COMMENT '转化率',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_stat_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日报表';

-- 6. 投注排行榜视图（最近24小时）
CREATE OR REPLACE VIEW v_recent_24h_top_orders AS
SELECT 
    order_id,
    player_id,
    username,
    game_name,
    bet_type,
    bet_amount,
    win_amount,
    profit_loss,
    bet_status,
    bet_time,
    TIMESTAMPDIFF(HOUR, bet_time, NOW()) as hours_ago
FROM dwd_bet_order_detail
WHERE bet_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY bet_amount DESC
LIMIT 100;

-- 7. 投注金额排行榜视图（Top 100）
CREATE OR REPLACE VIEW v_bet_amount_top100 AS
SELECT 
    player_id,
    username,
    COUNT(*) as total_bets,
    SUM(bet_amount) as total_bet_amount,
    SUM(win_amount) as total_win_amount,
    SUM(profit_loss) as total_profit_loss,
    AVG(bet_amount) as avg_bet_amount,
    MAX(bet_amount) as max_bet_amount,
    MIN(bet_time) as first_bet_time,
    MAX(bet_time) as last_bet_time
FROM dwd_bet_order_detail
WHERE bet_status IN (1, 2)  -- 只统计已开奖的订单
GROUP BY player_id, username
ORDER BY total_bet_amount DESC
LIMIT 100;

-- ============================================
-- 插入示例数据
-- ============================================

-- 插入玩家数据
INSERT INTO dim_player (player_id, username, nickname, email, phone, gender, age, country, province, city, register_time, vip_level, total_recharge, status) VALUES
(10001, 'player_zhang', '张三丰', 'zhang@example.com', '13800138001', 1, 28, '中国', '北京', '北京', '2024-01-15 10:30:00', 3, 50000.00, 1),
(10002, 'player_li', '李小龙', 'li@example.com', '13800138002', 1, 35, '中国', '上海', '上海', '2024-02-20 14:20:00', 5, 120000.00, 1),
(10003, 'player_wang', '王美丽', 'wang@example.com', '13800138003', 2, 25, '中国', '广东', '广州', '2024-03-10 09:15:00', 2, 30000.00, 1),
(10004, 'player_chen', '陈大文', 'chen@example.com', '13800138004', 1, 30, '中国', '浙江', '杭州', '2024-04-05 16:45:00', 4, 80000.00, 1),
(10005, 'player_liu', '刘德华', 'liu@example.com', '13800138005', 1, 42, '中国', '四川', '成都', '2024-05-12 11:20:00', 6, 200000.00, 1);

-- 插入投注订单数据（包含最近24小时的数据）
INSERT INTO dwd_bet_order_detail (order_id, player_id, username, game_id, game_name, bet_type, bet_amount, win_amount, profit_loss, odds, bet_status, order_status, bet_time, settle_time, ip_address, device_type) VALUES
-- 最近24小时内的订单
(20001, 10001, 'player_zhang', 1, '双色球', 'lottery', 100.00, 0.00, -100.00, 0.00, 2, 2, DATE_SUB(NOW(), INTERVAL 2 HOUR), DATE_SUB(NOW(), INTERVAL 1 HOUR), '192.168.1.100', 'web'),
(20002, 10002, 'player_li', 2, '英超联赛', 'sports', 5000.00, 8500.00, 3500.00, 1.70, 1, 2, DATE_SUB(NOW(), INTERVAL 5 HOUR), DATE_SUB(NOW(), INTERVAL 3 HOUR), '192.168.1.101', 'ios'),
(20003, 10003, 'player_wang', 3, '百家乐', 'casino', 2000.00, 0.00, -2000.00, 1.95, 2, 2, DATE_SUB(NOW(), INTERVAL 8 HOUR), DATE_SUB(NOW(), INTERVAL 7 HOUR), '192.168.1.102', 'android'),
(20004, 10004, 'player_chen', 1, '双色球', 'lottery', 500.00, 1500.00, 1000.00, 3.00, 1, 2, DATE_SUB(NOW(), INTERVAL 12 HOUR), DATE_SUB(NOW(), INTERVAL 10 HOUR), '192.168.1.103', 'web'),
(20005, 10005, 'player_liu', 4, 'NBA', 'sports', 10000.00, 0.00, -10000.00, 2.10, 2, 2, DATE_SUB(NOW(), INTERVAL 15 HOUR), DATE_SUB(NOW(), INTERVAL 13 HOUR), '192.168.1.104', 'web'),
(20006, 10001, 'player_zhang', 3, '百家乐', 'casino', 3000.00, 5700.00, 2700.00, 1.90, 1, 2, DATE_SUB(NOW(), INTERVAL 18 HOUR), DATE_SUB(NOW(), INTERVAL 16 HOUR), '192.168.1.100', 'web'),
(20007, 10002, 'player_li', 2, '英超联赛', 'sports', 8000.00, 0.00, -8000.00, 1.85, 2, 2, DATE_SUB(NOW(), INTERVAL 20 HOUR), DATE_SUB(NOW(), INTERVAL 18 HOUR), '192.168.1.101', 'ios'),
(20008, 10003, 'player_wang', 5, '轮盘赌', 'casino', 1500.00, 3000.00, 1500.00, 2.00, 1, 2, DATE_SUB(NOW(), INTERVAL 22 HOUR), DATE_SUB(NOW(), INTERVAL 20 HOUR), '192.168.1.102', 'android'),
-- 较早的订单
(20009, 10004, 'player_chen', 1, '双色球', 'lottery', 200.00, 0.00, -200.00, 0.00, 2, 2, '2024-11-01 10:00:00', '2024-11-01 12:00:00', '192.168.1.103', 'web'),
(20010, 10005, 'player_liu', 4, 'NBA', 'sports', 15000.00, 28500.00, 13500.00, 1.90, 1, 2, '2024-11-02 15:30:00', '2024-11-02 18:00:00', '192.168.1.104', 'web'),
(20011, 10001, 'player_zhang', 3, '百家乐', 'casino', 5000.00, 0.00, -5000.00, 1.95, 2, 2, '2024-11-03 09:20:00', '2024-11-03 10:00:00', '192.168.1.100', 'web'),
(20012, 10002, 'player_li', 2, '英超联赛', 'sports', 12000.00, 20400.00, 8400.00, 1.70, 1, 2, '2024-11-04 14:00:00', '2024-11-04 16:30:00', '192.168.1.101', 'ios');

-- 插入充值订单数据
INSERT INTO dwd_recharge_order (order_id, player_id, username, recharge_amount, actual_amount, bonus_amount, payment_method, currency, pay_status, order_time, pay_time, ip_address) VALUES
(30001, 10001, 'player_zhang', 1000.00, 1000.00, 100.00, 'alipay', 'CNY', 1, '2024-11-01 10:00:00', '2024-11-01 10:05:00', '192.168.1.100'),
(30002, 10002, 'player_li', 5000.00, 5000.00, 500.00, 'wechat', 'CNY', 1, '2024-11-02 15:30:00', '2024-11-02 15:35:00', '192.168.1.101'),
(30003, 10003, 'player_wang', 2000.00, 2000.00, 200.00, 'bank', 'CNY', 1, '2024-11-03 09:20:00', '2024-11-03 09:25:00', '192.168.1.102'),
(30004, 10004, 'player_chen', 10000.00, 10000.00, 1000.00, 'alipay', 'CNY', 1, '2024-11-04 14:00:00', '2024-11-04 14:05:00', '192.168.1.103'),
(30005, 10005, 'player_liu', 20000.00, 20000.00, 2000.00, 'crypto', 'CNY', 1, '2024-11-05 11:00:00', '2024-11-05 11:10:00', '192.168.1.104');
