/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * DEPENDENT 依赖时段（对齐 Dolphin dateValue 常用子集）
 */

export const DEPENDENT_DATE_OPTIONS: { label: string; value: string; cycle: string }[] = [
  { label: '当前小时 (currentHour)', value: 'currentHour', cycle: 'hour' },
  { label: '过去 1 小时 (last1Hour)', value: 'last1Hour', cycle: 'hour' },
  { label: '过去 2 小时 (last2Hours)', value: 'last2Hours', cycle: 'hour' },
  { label: '过去 3 小时 (last3Hours)', value: 'last3Hours', cycle: 'hour' },
  { label: '过去 24 小时 (last24Hours)', value: 'last24Hours', cycle: 'hour' },
  { label: '当天 (today)', value: 'today', cycle: 'day' },
  { label: '昨天 (yesterday)', value: 'yesterday', cycle: 'day' },
  { label: '过去 1 天 (last1Days)', value: 'last1Days', cycle: 'day' },
  { label: '过去 2 天 (last2Days)', value: 'last2Days', cycle: 'day' },
  { label: '过去 3 天 (last3Days)', value: 'last3Days', cycle: 'day' },
  { label: '过去 7 天 (last7Days)', value: 'last7Days', cycle: 'day' },
  { label: '本周 (thisWeek)', value: 'thisWeek', cycle: 'week' },
  { label: '上周 (lastWeek)', value: 'lastWeek', cycle: 'week' },
  { label: '本月 (thisMonth)', value: 'thisMonth', cycle: 'month' },
  { label: '上月 (lastMonth)', value: 'lastMonth', cycle: 'month' },
]

export function cycleForDateValue(dateValue: string): string {
  return DEPENDENT_DATE_OPTIONS.find(o => o.value === dateValue)?.cycle || 'day'
}

/** 从节点 params 解析 DEPENDENT 表单字段 */
export function dependentParamsToForm(params: any): {
  relation: 'AND' | 'OR'
  depend_items: { depend_workflow_id: number | null; date_value: string }[]
} {
  let dep: any = {}
  if (params && typeof params === 'object' && !Array.isArray(params)) {
    dep = params
  } else if (typeof params === 'string') {
    try { dep = JSON.parse(params) } catch { dep = {} }
  }
  const relation = (String(dep.relation || 'AND').toUpperCase() === 'OR' ? 'OR' : 'AND') as 'AND' | 'OR'
  if (Array.isArray(dep.depend_items) && dep.depend_items.length) {
    return {
      relation,
      depend_items: dep.depend_items.map((it: any) => ({
        depend_workflow_id: it?.depend_workflow_id ?? null,
        date_value: it?.date_value || 'today',
      })),
    }
  }
  return {
    relation,
    depend_items: [{
      depend_workflow_id: dep.depend_workflow_id ?? null,
      date_value: dep.date_value || 'today',
    }],
  }
}

export function dependentFormToParams(values: {
  relation?: string
  depend_items?: { depend_workflow_id?: number | null; date_value?: string }[]
}): Record<string, any> {
  const relation = String(values.relation || 'AND').toUpperCase() === 'OR' ? 'OR' : 'AND'
  const items = (values.depend_items || [])
    .filter(it => it && it.depend_workflow_id != null)
    .map(it => ({
      depend_workflow_id: Number(it.depend_workflow_id),
      cycle: cycleForDateValue(it.date_value || 'today'),
      date_value: it.date_value || 'today',
    }))
  if (!items.length) {
    throw new Error('请至少配置一条依赖（选择工作流）')
  }
  const first = items[0]
  return {
    relation,
    depend_items: items,
    // 兼容旧扁平字段
    depend_workflow_id: first.depend_workflow_id,
    cycle: first.cycle,
    date_value: first.date_value,
  }
}
