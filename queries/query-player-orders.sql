-- ============================================
-- 查询1: 查询玩家订单记录
-- 用途: 查看指定玩家的投注历史明细
-- ============================================

USE bigdata_dw;

-- 参数: @player_id (玩家ID)
SET @player_id = 10001;

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
    CASE o.order_status
        WHEN 0 THEN '待支付'
        WHEN 1 THEN '已支付'
        WHEN 2 THEN '已完成'
        WHEN 3 THEN '已取消'
        ELSE '未知'
    END AS '订单状态',
    o.bet_time AS '投注时间',
    o.settle_time AS '结算时间',
    o.device_type AS '设备类型',
    o.ip_address AS 'IP地址'
FROM dwd_bet_order_detail o
WHERE o.player_id = @player_id
ORDER BY o.bet_time DESC
LIMIT 100;

-- ============================================
-- 统计信息
-- ============================================
SELECT 
    COUNT(*) AS '总订单数',
    SUM(bet_amount) AS '总投注金额',
    SUM(win_amount) AS '总赢得金额',
    SUM(profit_loss) AS '总盈亏',
    AVG(bet_amount) AS '平均投注金额',
    MAX(bet_amount) AS '最大投注金额',
    MIN(bet_amount) AS '最小投注金额',
    SUM(CASE WHEN bet_status = 1 THEN 1 ELSE 0 END) AS '中奖次数',
    SUM(CASE WHEN bet_status = 2 THEN 1 ELSE 0 END) AS '未中奖次数',
    ROUND(SUM(CASE WHEN bet_status = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS '中奖率(%)'
FROM dwd_bet_order_detail
WHERE player_id = @player_id;
