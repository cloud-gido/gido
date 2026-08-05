---
name: gido-sql-publish-template
description: >-
  Unified GIDO Batch Studio SQL script header and comment template for new or
  published SQL tasks (Studio nodes / Dolphin publish). Use when creating SQL
  scripts, writing DWD/DWS/ADS ETL, publishing workflows to DolphinScheduler,
  or when the user asks for a SQL 提交模板 / 发布模板 / 脚本头注释.
---

# GIDO SQL 发布：统一提交模板

创建或改写 **Batch Studio SQL 节点**（将发布到 Dolphin）时，必须套用本模板：注释写全，但**不得**用会破坏 DS Doris 调度的注释写法。

## 为何有这套规则

- GIDO Studio 原样执行 SQL → 带 `/* */` 也能跑。
- Dolphin SQL（Doris 源）会去注释；块注释里一旦有 `/`（如「用户 / 站点」「笔数 / 分母」）易撕成 `***/`，任务失败（DS #17023）。
- GIDO 发布时会剥注释再推 DS（`_rewrite_sql_builtins` / `_strip_sql_comments_for_ds`），但 **Studio 源码仍须用安全注释**，以便手工贴到 DS、未重新发布的旧定义、或剥注释未部署时也不翻车。

## 硬性约定

1. **叙述性注释只用 `--` 行注释**，禁止业务说明用 `/* ... */`。
2. **禁止**在注释正文里写未转义的「斜杠叙述」依赖块注释（例如 `/* a / b */`）。若必须写「A/B」，用 `-- A 与 B` 或 `-- A 比 B`。
3. DDL 列/表上的 `COMMENT '...'` 是 Doris 语法，**保留**，不是 SQL 注释。
4. 可执行语句与文档头之间空一行；头注释与代码用 `-- ====...====` 分隔。
5. 新建脚本必须填齐下方「必填头字段」；改口径时更新「更新时间」与口径段。

## 必填头字段

| 字段 | 说明 |
|------|------|
| 脚本名称 | 与 Studio 节点名一致，建议 `层_域_对象`，如 `dwd_gameline_bet_combination` |
| Job / 节点 | 调度或 Doris JOB 名（若有），如 `jb_ads_...` |
| 作者 | 工号或域账号 |
| 创建时间 | `YYYY-MM-DD` |
| 更新时间 | `YYYY-MM-DD`（每次改口径必改） |
| 目标库表 | `db.table` |
| 刷新策略 | 调度周期 / 补数方式 / SLA |
| 数据来源 | 上游表列表 |
| 口径说明 | 过滤、派生指标、已产品确认点 |
| 前置依赖 | 类型、catalog、删除标记等 |

## 提交模板（新建 SQL 直接套用）

```sql
-- ============================================================================
-- 脚本名称 : <script_name>
-- Job / 节点 : <job_or_node_name>
-- 作者       : <author>
-- 创建时间   : <YYYY-MM-DD>
-- 更新时间   : <YYYY-MM-DD>
-- 目标库表   : <db.table>
-- 刷新策略   : <cron / 手动 / 补数说明>
-- 用户或粒度 : <如 operator_id + player_id；无则写 无>
--
-- 数据来源 :
--   - <db.table>   <一句话用途>
--   - <db.table>   <一句话用途>
--
-- 口径说明（已产品确认的请标注）:
--   - <过滤条件>
--   - <派生指标定义；比值用「比」或「除以」，勿在块注释里写斜杠>
--   - <排除状态或边界>
--
-- 前置依赖 :
--   - <如金额字段须为 DECIMAL；__deleted 语义等>
--
-- 说明 :
--   - <其它运维或后台展示注意点>
-- ============================================================================

USE <target_db>;

-- --------------------------------------------------------------------------
-- 可选：建表 / 重建（默认注释掉；需要时再打开对应行）
-- --------------------------------------------------------------------------
-- DROP TABLE IF EXISTS <table>;
-- CREATE TABLE <table> (
--     ...
-- )
-- UNIQUE KEY(...)
-- COMMENT '表中文说明'
-- DISTRIBUTED BY HASH(...) BUCKETS ...
-- PROPERTIES (...);

-- --------------------------------------------------------------------------
-- 可执行区：增量删除（若有）+ 主 INSERT/SELECT
-- --------------------------------------------------------------------------
-- DELETE FROM ... WHERE ... ;

INSERT INTO <table> (
    col_a,
    col_b
)
SELECT
    col_a,
    col_b
FROM <source>
WHERE 1 = 1
;
```

## Agent 写作检查清单

- [ ] 头字段齐全（脚本名、作者、创建/更新时间、目标表、来源、口径）
- [ ] 全程无业务用 `/* */`（仅允许用户明确要求且已知已部署剥注释发布时例外；默认仍用 `--`）
- [ ] 比值、路径类文字未写成块注释内的 `a / b`
- [ ] `USE` + 主 DML 可独立执行；`--` 掉的 DDL 不阻碍日常调度
- [ ] 提醒用户：改脚本后须 **保存并重新发布生产**，DS 才会拿到剥注释后的新定义

## 与「能跑的 DWD 脚本」对齐

参考已验证可过 Dolphin 的风格：`--` 分区标题 + `COMMENT '列说明'` + 无块注释横幅。ADS 类宽表同样套本模板，不要用 `/*** ... ***/` 横幅。

## 产品自动预填

Batch Studio **新建 SQL 节点**时，前端会写入本模板骨架（脚本名=节点名，作者=当前用户，创建/更新时间=当天）：

- `gido/frontend/src/utils/sqlPublishTemplate.ts` → `buildDefaultSqlPublishScript`
- 接线：`gido/frontend/src/pages/Studio.tsx` `handleCreate`

占位符（目标库表、来源、口径等）仍须开发者补全后再发布。

## 相关代码

- 发布剥注释：`gido/backend/app/services/dolphin.py`（`_strip_sql_comments_for_ds`、`_rewrite_sql_builtins`）
- Studio SQL 节点 → DS：`sync_workflow` 内 SQL `taskParams.sql`
