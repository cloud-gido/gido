-- ============================================
-- 查询3: 查询投注金额 Top 100 订单（排行榜）
-- 用途: 识别高价值玩家，VIP客户管理
-- ============================================

USE bigdata_dw;

SELECT 
    @rank := @rank + 1 AS '排名',
    t.player_id AS '玩家ID',
    t.username AS '用户名',
    t.total_bets AS '总投注次数',
    t.total_bet_amount AS '总投注金额',
    t.total_win_amount AS '总赢得金额',
    t.total_profit_loss AS '总盈亏',
    t.avg_bet_amount AS '平均投注金额',
    t.max_bet_amount AS '最大单笔投注',
    t.first_bet_time AS '首次投注时间',
    t.last_bet_time AS '最后投注时间',
    p.vip_level AS 'VIP等级',
    p.total_recharge AS '累计充值',
    p.city AS '城市',
    p.province AS '省份'
FROM (
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
    LIMIT 100
) t,
dim_player p,
(SELECT @rank := 0) r
WHERE t.player_id = p.player_id
ORDER BY t.total_bet_amount DESC;

-- ============================================
-- Top 100 玩家统计汇总
-- ============================================
SELECT 
    COUNT(*) AS '玩家数量',
    SUM(total_bet_amount) AS '总投注金额',
    AVG(total_bet_amount) AS '人均投注金额',
    SUM(total_win_amount) AS '总派奖金额',
    SUM(total_profit_loss) AS '平台总盈亏',
    AVG(avg_bet_amount) AS '平均单笔投注',
    MAX(max_bet_amount) AS '最大单笔投注',
    ROUND(SUM(total_win_amount) * 100.0 / SUM(total_bet_amount), 2) AS '整体派奖率(%)',
    COUNT(DISTINCT CASE WHEN vip_level >= 3 THEN player_id END) AS '高等级VIP数'
FROM (
    SELECT 
        t.player_id,
        t.total_bet_amount,
        t.total_win_amount,
        t.total_profit_loss,
        t.avg_bet_amount,
        t.max_bet_amount,
        p.vip_level
    FROM (
        SELECT 
            player_id,
            SUM(bet_amount) as total_bet_amount,
            SUM(win_amount) as total_win_amount,
            SUM(profit_loss) as total_profit_loss,
            AVG(bet_amount) as avg_bet_amount,
            MAX(bet_amount) as max_bet_amount
        FROM dwd_bet_order_detail
        WHERE bet_status IN (1, 2)
        GROUP BY player_id
        ORDER BY total_bet_amount DESC
        LIMIT 100
    ) t
    JOIN dim_player p ON t.player_id = p.player_id
) summary;

-- ============================================
-- 按VIP等级分布
-- ============================================
SELECT 
    p.vip_level AS 'VIP等级',
    COUNT(*) AS '玩家数',
    SUM(t.total_bet_amount) AS '总投注金额',
    AVG(t.total_bet_amount) AS '人均投注',
    SUM(t.total_win_amount) AS '总派奖',
    SUM(t.total_profit_loss) AS '平台盈亏'
FROM (
    SELECT 
        player_id,
        SUM(bet_amount) as total_bet_amount,
        SUM(win_amount) as total_win_amount,
        SUM(profit_loss) as total_profit_loss
    FROM dwd_bet_order_detail
    WHERE bet_status IN (1, 2)
    GROUP BY player_id
    ORDER BY total_bet_amount DESC
    LIMIT 100
) t
JOIN dim_player p ON t.player_id = p.player_id
GROUP BY p.vip_level
ORDER BY p.vip_level DESC;
