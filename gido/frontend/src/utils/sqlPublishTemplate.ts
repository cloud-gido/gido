/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Batch Studio 新建 SQL 节点默认脚本头（与 .cursor/skills/gido-sql-publish-template 对齐）。
 * 叙述性注释只用 --，避免 Dolphin Doris 块注释去注释翻车。
 */

export type SqlPublishTemplateInput = {
  scriptName?: string | null
  author?: string | null
  /** YYYY-MM-DD；缺省取本地当天 */
  createdAt?: string | null
  jobName?: string | null
}

function todayLocalYmd(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function field(v: string | null | undefined, placeholder: string): string {
  const s = (v || '').trim()
  return s || placeholder
}

/** 新建 SQL 节点写入的默认 script_content */
export function buildDefaultSqlPublishScript(input: SqlPublishTemplateInput = {}): string {
  const name = field(input.scriptName, '<脚本名称>')
  const author = field(input.author, '<作者>')
  const day = field(input.createdAt, todayLocalYmd())
  const job = field(input.jobName, name)

  return [
    '-- ============================================================================',
    `-- 脚本名称 : ${name}`,
    `-- Job / 节点 : ${job}`,
    `-- 作者       : ${author}`,
    `-- 创建时间   : ${day}`,
    `-- 更新时间   : ${day}`,
    '-- 目标库表   : <db.table>',
    '-- 刷新策略   : <cron / 手动 / 补数说明>',
    '-- 用户或粒度 : <如 operator_id + player_id；无则写 无>',
    '--',
    '-- 数据来源 :',
    '--   - <db.table>   <一句话用途>',
    '--',
    '-- 口径说明（已产品确认的请标注）:',
    '--   - <过滤条件>',
    '--   - <派生指标；比值用「比」或「除以」，勿写块注释>',
    '--',
    '-- 前置依赖 :',
    '--   - <类型、catalog、删除标记等>',
    '--',
    '-- 说明 :',
    '--   - <运维或后台展示注意点>',
    '-- ============================================================================',
    '',
    'USE <target_db>;',
    '',
    '-- --------------------------------------------------------------------------',
    '-- 可选：建表 / 重建（默认注释掉；需要时再打开对应行）',
    '-- --------------------------------------------------------------------------',
    '-- DROP TABLE IF EXISTS <table>;',
    '-- CREATE TABLE <table> (',
    '--     id BIGINT NOT NULL COMMENT \'主键\'',
    '-- )',
    '-- UNIQUE KEY(id)',
    '-- COMMENT \'表中文说明\'',
    '-- DISTRIBUTED BY HASH(id) BUCKETS 8',
    '-- PROPERTIES (',
    '--     "replication_allocation" = "tag.location.default: 3"',
    '-- );',
    '',
    '-- --------------------------------------------------------------------------',
    '-- 可执行区：增量删除（若有）+ 主 INSERT 或 SELECT',
    '-- --------------------------------------------------------------------------',
    '-- DELETE FROM <table> WHERE ... ;',
    '',
    'SELECT 1 AS ok;',
    '',
  ].join('\n')
}
