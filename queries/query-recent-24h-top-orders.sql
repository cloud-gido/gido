-- ============================================
-- 查询2: 查询最近24小时 Top 100 订单
-- 用途: 监控近期大额投注，风控预警
-- ============================================

USE bigdata_dw;

SELECT 
    o.order_id AS '订单ID',
    o.player_id AS '玩家ID',
    o.username AS '用户名',
    o.game_name AS '游戏名称',
    o.bet_type AS '投注类型',
    o.bet_amount AS '投注金额',
    o.win_amount AS '赢得金额',
    o.profit_loss AS '盈亏金额',
    o.odds AS '赔率',
    CASE o.bet_status
        WHEN 0 THEN '待开奖'
        WHEN 1 THEN '已中奖'
        WHEN 2 THEN '未中奖'
        WHEN 3 THEN '已取消'
        WHEN 4 THEN '退款'
        ELSE '未知'
    END AS '投注状态',
    o.bet_time AS '投注时间',
    TIMESTAMPDIFF(HOUR, o.bet_time, NOW()) AS '距现在(小时)',
    o.device_type AS '设备类型',
    o.ip_address AS 'IP地址'
FROM dwd_bet_order_detail o
WHERE o.bet_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY o.bet_amount DESC
LIMIT 100;

-- ============================================
-- 最近24小时统计汇总
-- ============================================
SELECT 
    COUNT(*) AS '总订单数',
    COUNT(DISTINCT player_id) AS '活跃玩家数',
    SUM(bet_amount) AS '总投注金额',
    AVG(bet_amount) AS '平均投注金额',
    MAX(bet_amount) AS '最大投注金额',
    SUM(win_amount) AS '总派奖金额',
    SUM(profit_loss) AS '平台盈亏',
    SUM(CASE WHEN bet_status = 1 THEN 1 ELSE 0 END) AS '中奖订单数',
    ROUND(SUM(CASE WHEN bet_status = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS '中奖率(%)',
    COUNT(DISTINCT game_name) AS '游戏数量',
    COUNT(DISTINCT bet_type) AS '投注类型数'
FROM dwd_bet_order_detail
WHERE bet_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================
-- 按游戏类型统计
-- ============================================
SELECT 
    o.bet_type AS '投注类型',
    COUNT(*) AS '订单数',
    COUNT(DISTINCT o.player_id) AS '玩家数',
    SUM(o.bet_amount) AS '投注金额',
    AVG(o.bet_amount) AS '平均投注',
    SUM(o.win_amount) AS '派奖金额',
    SUM(o.profit_loss) AS '平台盈亏'
FROM dwd_bet_order_detail o
WHERE o.bet_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY o.bet_type
ORDER BY SUM(o.bet_amount) DESC;
