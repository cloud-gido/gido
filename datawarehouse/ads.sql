-- 7.1 用户概况API统计表
CREATE TABLE IF NOT EXISTS ads_user_overview (
    -- ==================== 主键/维度 ====================
    player_id VARCHAR(64) COMMENT '玩家业务ID(主键,对应DTO.userId,避免JS精度丢失)',
    operator_id BIGINT COMMENT '运营商ID(联合主键,支持多租户隔离,对应bet_order.operator_id)',

    -- ==================== 用户基本信息 (完全对应 UserOverviewResponse DTO) ====================
    account_id BIGINT COMMENT '账户ID(对应DTO.accountId,来自account表)',
    player_name VARCHAR(128) COMMENT '玩家姓名(对应DTO.username,来自account.player_name)',
    status VARCHAR(32) COMMENT '用户状态(ACTIVE-活跃/FROZEN-冻结/CLOSED-关闭,对应DTO.status,来自account.status)',

    -- ==================== 投注统计指标 (对应SQL聚合逻辑) ====================
    user_stake_amount_all DECIMAL(18,6) COMMENT '累计投注总额(计算公式: SUM(bc.stake),排除REJECTED/CANCELLED订单,对应DTO.betAmount)',
    users_profit_amount_all DECIMAL(18,6) COMMENT '累计赔付总额(计算公式: SUM(bc.payout_amount),即用户赢得的金额,对应DTO.winAmount)',
    users_loss_amount_all DECIMAL(18,6) COMMENT '累计亏损总额(计算公式: SUM(os.stake - os.payout),即平台盈利金额,正值表示平台盈利,负值表示用户盈利,对应DTO.lossAmount)',
    users_profit_precent DECIMAL(10,4) COMMENT '盈利率百分比(计算公式: ROUND(SUM(os.stake - os.payout) / NULLIF(SUM(os.stake), 0) * 100, 2),对应DTO.profitRate)',
    order_count BIGINT COMMENT '有效订单总数(计算公式: COUNT(DISTINCT bo.order_id),排除REJECTED/CANCELLED)',

    -- ==================== 时间指标 ====================
    last_create_at DATETIMEV2 COMMENT '最后投注时间(计算公式: MAX(bo.created_at),对应DTO.lastBetTime)',

    -- ==================== 赛事维度信息 ====================
    organizer_list STRING COMMENT '主办方列表(计算公式: GROUP_CONCAT(DISTINCT competition_name SEPARATOR '',''),逗号分隔的联赛名称,如: 英超,西甲,意甲,对应DTO.organizer)',

    -- ==================== 排序优化字段 (冗余字段,加速排序查询) ====================
    sort_by_stake DECIMAL(18,6) COMMENT '按投注额排序冗余字段(与user_stake_amount_all相同,用于加速ORDER BY betAmount查询)',
    sort_by_profit DECIMAL(18,6) COMMENT '按盈利排序冗余字段(与users_loss_amount_all相同,用于加速ORDER BY lossAmount查询)',

    -- ==================== 统计元数据 ====================
    stat_date DATE COMMENT '统计日期(分区键,T+1离线计算日期,用于历史快照和增量更新)',

    -- ==================== 数仓元数据 ====================
    dw_created_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录创建时间(ETL加载时间)',
    dw_updated_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录更新时间(最后修改时间)'
) ENGINE=OLAP
UNIQUE KEY(player_id, operator_id)
COMMENT 'ADS层-用户概况表(完全对应GET /api/v1/user-overview接口和UserOverviewResponse DTO,包含用户投注统计、盈利率、主办方列表等核心指标,支持多维度排序和分页查询)'
PARTITION BY RANGE(stat_date) ()
DISTRIBUTED BY HASH(operator_id, player_id) BUCKETS 8
PROPERTIES (
    "replication_num" = "3",                          -- 副本数,保证高可用和数据安全
    "enable_unique_key_merge_on_write" = "true",      -- 写入时合并,支持每日增量更新
    "light_schema_change" = "true",                   -- 轻量级Schema变更,支持在线添加字段
    "dynamic_partition.enable" = "true",              -- 动态分区,自动管理历史数据
    "dynamic_partition.time_unit" = "DAY",            -- 按日分区
    "dynamic_partition.start" = "-30",                -- 保留最近30天分区
    "dynamic_partition.end" = "3",                    -- 预创建未来3天分区
    "bloom_filter_columns" = "player_id,operator_id,status",  -- Bloom Filter加速等值过滤
    "compression" = "LZ4"                             -- 压缩算法,LZ4平衡压缩率和性能
);

-- ==================== 完整索引策略（6个索引）====================

-- 索引1: 按运营商+投注额排序 (最常用场景,对应默认排序)
CREATE INDEX idx_ads_user_operator_stake ON ads_user_overview (operator_id, sort_by_stake DESC)
COMMENT '按运营商查询用户概况列表并按投注额降序排序,对应sortField=betAmount&sortOrder=DESC';

-- 索引2: 按运营商+盈利率排序 (查询高价值/高风险用户)
CREATE INDEX idx_ads_user_operator_profit ON ads_user_overview (operator_id, sort_by_profit DESC)
COMMENT '按运营商查询并按盈利率排序,对应sortField=lossAmount或profitRate';

-- 索引3: 按运营商+最后投注时间排序 (查询活跃用户)
CREATE INDEX idx_ads_user_operator_last_bet ON ads_user_overview (operator_id, last_create_at DESC)
COMMENT '按运营商查询并按最后投注时间排序,对应sortField=lastBetTime';

-- 索引4: 按运营商+用户状态过滤 (查询活跃/冻结用户)
CREATE INDEX idx_ads_user_operator_status ON ads_user_overview (operator_id, status, sort_by_stake DESC)
COMMENT '按运营商和用户状态过滤,如查询所有活跃用户并按投注额排序';

-- 索引5: 按玩家ID精确查询 (单个用户详情,跨运营商)
CREATE INDEX idx_ads_user_player ON ads_user_overview (player_id)
COMMENT '按玩家ID精确查询,用于单个用户概况查询或跨运营商查询';

-- 索引6: 按运营商+玩家ID联合查询 (最精确的查询)
CREATE INDEX idx_ads_user_operator_player ON ads_user_overview (operator_id, player_id)
COMMENT '按运营商和玩家ID联合查询,用于指定运营商下的单个用户查询';


-- 7.2 投注Feed流缓存表
CREATE TABLE IF NOT EXISTS ads_betting_feed_cache (
    -- ==================== 主键/维度 ====================
    operator_id BIGINT COMMENT '运营商ID(联合主键,支持多租户隔离)',
    cache_key VARCHAR(128) COMMENT '缓存Key(格式: betting_feed_all_{operator_id} 或 betting_feed_leaderboard_{operator_id}_{window_hours},用于区分不同Feed类型)',
    row_order INT COMMENT '行顺序(用于保持Feed流的展示顺序,对应分页Step1的orderIds顺序)',
    order_id BIGINT COMMENT '订单ID(业务主键,对应DTO.orderId)',

    -- ==================== 赛事信息 (完全对应 BettingFeedRowDTO) ====================
    fixture_id BIGINT COMMENT '赛事ID(对应DTO.fixtureId,来自displayLeg.fixtureId)',
    home_team VARCHAR(128) COMMENT '主队名称(对应DTO.homeTeam,从fixture.competitors JSON提取Home队伍)',
    away_team VARCHAR(128) COMMENT '客队名称(对应DTO.awayTeam,从fixture.competitors JSON提取Away队伍)',
    match_name VARCHAR(256) COMMENT '赛事名称(对应DTO.matchName,格式: 联赛名 主队 vs 客队,如: 英超 曼联 vs 利物浦)',
    competition_name VARCHAR(128) COMMENT '联赛名称(用于构建match_name,从fixture.competition JSON提取)',

    -- ==================== 用户信息 ====================
    player_id VARCHAR(64) COMMENT '玩家业务ID(对应DTO.playerName,实际存储的是playerId而非姓名)',
    player_name VARCHAR(128) COMMENT '玩家显示名称(可选,如需显示真实姓名可从此字段获取,当前实现中使用playerId)',

    -- ==================== 投注信息 (核心指标) ====================
    total_stake DECIMAL(18,6) COMMENT '投注金额(对应DTO.totalStake,来自bet_order.total_stake)',
    total_odds DECIMAL(10,6) COMMENT '总赔率(对应DTO.totalOdds,来自bet_fixture_price_snapshot.payout_price,处理中订单使用快照赔率)',

    -- ==================== 订单状态 ====================
    order_status VARCHAR(16) COMMENT '订单展示状态(对应DTO.orderStatus,IN_PROGRESS-进行中/COMPLETED-已完成)',
    bet_status VARCHAR(32) COMMENT '订单原始状态(对应DTO.betStatus,CREATED/PENDING/CONFIRMED/SETTLED/CANCELLED等)',

    -- ==================== 时间字段 ====================
    created_at DATETIMEV2 COMMENT '订单创建时间(对应DTO.createdAt,UTC时区,用户投注时刻)',

    -- ==================== 缓存管理 ====================
    cache_expire_at DATETIMEV2 COMMENT '缓存过期时间(用于定时清理过期缓存数据,默认TTL=300秒)',

    -- ==================== 数仓元数据 ====================
    dw_created_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录创建时间(ETL加载时间)',
    dw_updated_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录更新时间(最后修改时间)'
) ENGINE=OLAP
UNIQUE KEY(operator_id, cache_key, row_order)
COMMENT 'ADS层-投注Feed流缓存表(完全对应POST /api/v1/merchant-web/orders/betting-feed/all和leaderboard接口,缓存最近N条投注记录,提升高并发查询性能)'
DISTRIBUTED BY HASH(operator_id, cache_key) BUCKETS 8
PROPERTIES (
    "replication_num" = "3",                          -- 副本数,保证高可用
    "enable_unique_key_merge_on_write" = "true",      -- 写入时合并,支持实时更新缓存
    "light_schema_change" = "true",                   -- 轻量级Schema变更
    "bloom_filter_columns" = "operator_id,cache_key,order_id,player_id",  -- Bloom Filter加速过滤
    "compression" = "LZ4",                            -- 压缩算法
    "storage_medium" = "SSD"                          -- 存储介质,SSD提升随机读取性能
);

-- ==================== 索引优化 ====================

-- 索引1: 按运营商+缓存Key查询 (最常用场景,对应 getBettingFeedAll API)
CREATE INDEX idx_ads_feed_operator_cache ON ads_betting_feed_cache (operator_id, cache_key, row_order ASC)
COMMENT '按运营商和缓存Key查询Feed流,并按row_order升序返回,对应betting-feed/all接口';

-- 索引2: 按订单ID精确查询 (对应 getBettingFeedByOrderId 内部接口)
CREATE INDEX idx_ads_feed_order ON ads_betting_feed_cache (order_id)
COMMENT '按订单ID精确查询,用于内部接口betting-feed/by-order';

-- 索引3: 按玩家ID查询 (查询某玩家的投注历史)
CREATE INDEX idx_ads_feed_player ON ads_betting_feed_cache (operator_id, player_id, created_at DESC)
COMMENT '按运营商和玩家ID查询投注历史,用于个人投注记录查询';

-- 索引4: 按赛事ID查询 (查询某赛事的所有投注)
CREATE INDEX idx_ads_feed_fixture ON ads_betting_feed_cache (operator_id, fixture_id, created_at DESC)
COMMENT '按运营商和赛事ID查询投注,用于热门赛事投注监控';


-- 7.3 订单详情宽表
CREATE TABLE IF NOT EXISTS ads_order_detail_wide (
    -- ==================== 主键 ====================
    order_id BIGINT COMMENT '订单ID(主键,唯一标识,对应DTO.orderId)',

    -- ==================== 用户与运营商信息 (冗余字段,避免JOIN) ====================
    player_sk BIGINT COMMENT '玩家代理键(关联dim_player,用于分区查询)',
    player_id VARCHAR(64) COMMENT '玩家业务ID',
    player_name VARCHAR(128) COMMENT '玩家姓名(冗余字段,避免JOIN dim_player)',
    operator_sk BIGINT COMMENT '运营商代理键(关联dim_operator)',
    operator_id BIGINT COMMENT '运营商业务ID',
    operator_name VARCHAR(128) COMMENT '运营商名称(冗余字段,避免JOIN dim_operator)',

    -- ==================== 订单基础信息 (完全对应 OrderInfoResponseDTO) ====================
    bet_id VARCHAR(64) COMMENT 'GTS BetID(全局投注系统订单ID,用于对账和问题追踪)',
    system_bet_type VARCHAR(32) COMMENT '系统投注类型(SINGLE-单关/MULTI-串关/SAME_GAME_MULTI-同场多选/SYSTEM-系统投注,对应DTO.systemBetType)',
    bet_type VARCHAR(32) COMMENT '投注业务类型(SINGLE/PARLAY等,对应DTO.betType)',
    order_status_display VARCHAR(16) COMMENT '订单展示状态(IN_PROGRESS-进行中/COMPLETED-已完成,对应DTO.orderStatus)',
    settlement_status VARCHAR(64) COMMENT '结算状态详情(多场次结算状态逗号拼接,如: WIN,LOSE,VOID,对应DTO.settlementStatus)',

    -- ==================== 金额信息 (完全对应 OrderInfoResponseDTO) ====================
    total_stake DECIMAL(18,6) COMMENT '总投注金额(用户实际下注金额,单位:币种,对应DTO.totalStake)',
    total_odds DECIMAL(10,6) COMMENT '总赔率(多场次赔率连乘结果,处理中订单使用snapshot赔率,已结算订单使用combination赔率,对应DTO.totalOdds)',
    expected_payout DECIMAL(18,6) COMMENT '预期赔付金额(计算公式: total_stake * total_odds,仅处理中订单有效,对应DTO.expectedPayout)',
    return_amount DECIMAL(18,6) COMMENT '实际返回金额(计算公式: total_stake * total_odds,仅已结算订单有效,对应DTO.returnAmount)',
    cash_out_amount DECIMAL(18,6) COMMENT '现金退出金额(用户选择提前退出时获得的金额,未现金退出时为NULL,对应DTO.cashOutAmount)',

    -- ==================== 赛事信息 (JSON存储,对应DTO.fixtureGroups) ====================
    fixture_groups_json STRING COMMENT '赛事分组JSON数组,完全对应DTO.fixtureGroups结构。示例: [{"fixtureId":123,"competitionName":"英超","countryName":"英格兰","homeTeam":"曼联","awayTeam":"利物浦","startTimeUtc":"2026-01-15T20:00:00Z","currentPhase":"FullTime","homeScore":2,"awayScore":1,"markets":[{"marketId":"100","marketName":"Match Winner","cashOutEnabled":true,"legs":[{"legId":"1","selectionId":"1001","selectionName":"Home","price":1.95,"payoutPrice":1.95,"resultStatus":"WIN"}]}]}]',

    -- ==================== 缓存字段 (从 fixture_groups_json 提取,加速列表查询) ====================
    competition_name VARCHAR(128) COMMENT '联赛名称(从fixture_groups_json提取第一个非空值,对应DTO.competitionName,避免解析JSON)',
    country_name VARCHAR(64) COMMENT '赛事国家/地区(从fixture_groups_json提取第一个非空值,对应DTO.countryName,避免解析JSON)',

    -- ==================== 计算字段 (Service层逻辑) ====================
    cash_out_enabled BOOLEAN COMMENT '订单级是否支持现金退出(仅当所有盘口cashOutEnabled均为true时为true,对应DTO.cashOutEnabled)',

    -- ==================== 时间字段 ====================
    created_at DATETIMEV2 COMMENT '订单创建时间(UTC时区,用户投注时刻,对应DTO.createdAt)',

    -- ==================== 数仓元数据 ====================
    dw_created_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录创建时间(ETL加载时间)',
    dw_updated_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录更新时间(最后修改时间)'
) ENGINE=OLAP
UNIQUE KEY(order_id)
COMMENT 'ADS层-订单详情宽表(完全对应OrderInfoResponseDTO,包含组装后的完整订单信息,支持按订单ID/玩家/运营商快速查询,冗余关键字段避免JOIN和JSON解析)'
DISTRIBUTED BY HASH(order_id) BUCKETS 16
PROPERTIES (
    "replication_num" = "3",                          -- 副本数,保证高可用和数据安全
    "enable_unique_key_merge_on_write" = "true",      -- 写入时合并,支持订单状态实时更新
    "light_schema_change" = "true",                   -- 轻量级Schema变更,支持在线添加字段
    "bloom_filter_columns" = "order_id,bet_id,player_id,operator_id,order_status_display",  -- Bloom Filter加速等值过滤
    "compression" = "LZ4",                            -- 压缩算法,LZ4平衡压缩率和查询性能
    "storage_medium" = "SSD"                          -- 存储介质,SSD提升随机读取性能
);

-- ==================== 索引优化 (加速常用查询场景) ====================

-- 索引1: 按玩家查询订单列表 (最常用场景,对应 getPlayerOrders API)
CREATE INDEX idx_ads_order_player ON ads_order_detail_wide (player_sk, created_at DESC)
COMMENT '按玩家查询订单列表,支持分页和时间范围筛选,对应OrderInfoPageAssembler.assembleByOrder';

-- 索引2: 按运营商查询订单 (运营后台使用)
CREATE INDEX idx_ads_order_operator ON ads_order_detail_wide (operator_sk, created_at DESC)
COMMENT '按运营商查询订单,用于运营监控和报表';

-- 索引3: 按订单状态查询 (筛选进行中/已完成订单)
CREATE INDEX idx_ads_order_status ON ads_order_detail_wide (order_status_display, created_at DESC)
COMMENT '按订单状态筛选,如查询所有IN_PROGRESS订单';

-- 索引4: 按GTS BetID查询 (对账和问题排查)
CREATE INDEX idx_ads_order_bet_id ON ads_order_detail_wide (bet_id)
COMMENT '按GTS BetID精确查询,用于对账和问题追踪';

-- 索引5: 按联赛名称查询 (快速浏览某联赛订单)
CREATE INDEX idx_ads_order_competition ON ads_order_detail_wide (competition_name, created_at DESC)
COMMENT '按联赛名称查询订单,如查询所有英超订单';


-- 7.4 运营大盘统计宽表
CREATE TABLE IF NOT EXISTS ads_operation_dashboard (
    -- ==================== 主键/维度 ====================
    stat_date DATE COMMENT '统计日期(分区键,T+1离线计算日期)',
    operator_id BIGINT COMMENT '运营商ID(业务主键,对应bet_order.operator_id)',

    -- ==================== 用户指标 ====================
    total_user_count BIGINT SUM COMMENT '累计注册用户数(计算公式: COUNT(DISTINCT player_id) FROM account WHERE created_at <= stat_date)',
    active_user_count BIGINT SUM COMMENT '当日活跃用户数(计算公式: COUNT(DISTINCT player_id) FROM bet_order WHERE DATE(created_at) = stat_date AND bet_status NOT IN (''REJECTED'',''CANCELLED''))',
    new_user_count BIGINT SUM COMMENT '当日新增用户数(计算公式: COUNT(DISTINCT player_id) FROM account WHERE DATE(created_at) = stat_date)',

    -- ==================== 订单指标 ====================
    total_order_count BIGINT SUM COMMENT '当日订单总量(计算公式: COUNT(order_id) FROM bet_order WHERE DATE(created_at) = stat_date)',
    valid_order_count BIGINT SUM COMMENT '当日有效订单数(计算公式: COUNT(order_id) FROM bet_order WHERE DATE(created_at) = stat_date AND bet_status NOT IN (''REJECTED'',''CANCELLED''))',
    confirmed_order_count BIGINT SUM COMMENT '当日确认订单数(计算公式: COUNT(order_id) FROM bet_order WHERE DATE(created_at) = stat_date AND bet_status = ''CONFIRMED'')',
    settled_order_count BIGINT SUM COMMENT '当日结算订单数(计算公式: COUNT(order_id) FROM bet_order WHERE DATE(settled_at) = stat_date AND bet_status = ''SETTLED'')',

    -- ==================== 金额指标 ====================
    total_bet_amount DECIMAL(18,6) SUM COMMENT '当日投注总额(计算公式: SUM(total_stake) FROM bet_order WHERE DATE(created_at) = stat_date AND bet_status NOT IN (''REJECTED'',''CANCELLED''))',
    total_payout_amount DECIMAL(18,6) SUM COMMENT '当日赔付总额(计算公式: SUM(bc.payout_amount) FROM bet_order bo JOIN bet_combination bc WHERE DATE(bo.created_at) = stat_date AND bo.bet_status = ''SETTLED'')',
    total_profit_amount DECIMAL(18,6) SUM COMMENT '当日平台盈利总额(计算公式: total_bet_amount - total_payout_amount,正值表示平台盈利,负值表示平台亏损)',

    -- ==================== 比率指标 (使用 MAX 取最新计算值) ====================
    bet_order_rate DECIMAL(10,4) MAX COMMENT '订单有效率(计算公式: valid_order_count / total_order_count * 100,排除系统自动取消的订单)',
    settlement_rate DECIMAL(10,4) MAX COMMENT '结算完成率(计算公式: settled_order_count / confirmed_order_count * 100)',
    avg_bet_amount DECIMAL(18,6) MAX COMMENT '人均投注金额(计算公式: total_bet_amount / active_user_count)',
    avg_order_per_user DECIMAL(10,4) MAX COMMENT '人均订单数(计算公式: valid_order_count / active_user_count)',
    profit_rate DECIMAL(10,4) MAX COMMENT '平台盈利率(计算公式: total_profit_amount / total_bet_amount * 100)',

    -- ==================== 投注类型分布 (可选,用于细分分析) ====================
    single_order_count BIGINT SUM COMMENT '单关订单数(计算公式: COUNT WHERE system_bet_type = ''SINGLE'')',
    parlay_order_count BIGINT SUM COMMENT '串关订单数(计算公式: COUNT WHERE system_bet_type = ''MULTI'' OR ''PARLAY'')',
    same_game_multi_count BIGINT SUM COMMENT '同场多选订单数(计算公式: COUNT WHERE system_bet_type = ''SAME_GAME_MULTI'')',
    system_bet_count BIGINT SUM COMMENT '系统投注订单数(计算公式: COUNT WHERE system_bet_type = ''SYSTEM'')',

    -- ==================== 时段分布 (可选,用于高峰分析) ====================
    peak_hour INT MAX COMMENT '投注高峰小时(0-23,计算公式: MODE(HOUR(created_at)))',
    peak_hour_order_count BIGINT MAX COMMENT '高峰小时订单数',

    -- ==================== 数仓元数据 ====================
    dw_created_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录创建时间(ETL加载时间)',
    dw_updated_at DATETIMEV2 DEFAULT CURRENT_DATETIME COMMENT '数仓记录更新时间(最后修改时间)'
) ENGINE=OLAP
AGGREGATE KEY(stat_date, operator_id)
COMMENT 'ADS层-运营大盘统计表(按日期+运营商维度汇总核心运营指标,支持运营监控、趋势分析、KPI考核)'
PARTITION BY RANGE(stat_date) ()
DISTRIBUTED BY HASH(operator_id) BUCKETS 8
PROPERTIES (
    "replication_num" = "3",                          -- 副本数,保证高可用
    "dynamic_partition.enable" = "true",              -- 动态分区,自动管理历史数据
    "dynamic_partition.time_unit" = "DAY",            -- 按日分区
    "dynamic_partition.start" = "-90",                -- 保留最近90天分区(运营通常需要季度数据)
    "dynamic_partition.end" = "3",                    -- 预创建未来3天分区
    "compression" = "LZ4",                            -- 压缩算法
    "bloom_filter_columns" = "stat_date,operator_id"  -- Bloom Filter加速过滤
);

-- ==================== Rollup 索引 (加速常用查询) ====================

-- Rollup 1: 按运营商汇总 (查看某运营商的整体表现)
ALTER TABLE ads_operation_dashboard
ADD ROLLUP rollup_operator_summary (
    operator_id,
    stat_date,
    total_user_count,
    active_user_count,
    total_bet_amount,
    total_profit_amount,
    profit_rate
) COMMENT '按运营商快速汇总';

-- Rollup 2: 按日期汇总 (查看所有运营商的每日总计)
ALTER TABLE ads_operation_dashboard
ADD ROLLUP rollup_daily_total (
    stat_date,
    total_user_count,
    active_user_count,
    total_order_count,
    total_bet_amount,
    total_profit_amount
) COMMENT '按日期快速汇总所有运营商';
