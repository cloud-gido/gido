/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Alert, Spin } from 'antd'
import type { InteractiveRunStatement } from '../types/interactiveRun'
import { statementObjectName, statementPresentation } from '../utils/statementPresentation'

export default function StatementExecutionSummary({
  statement,
}: {
  statement: InteractiveRunStatement
}) {
  const presentation = statementPresentation(statement)
  if (['pending', 'queued', 'running'].includes(statement.status || '')) {
    return <div style={{ padding: 24 }}><Spin /> <span style={{ marginLeft: 8 }}>{presentation.type} 正在执行…</span></div>
  }
  if (statement.error || statement.status === 'failed') {
    return (
      <Alert
        type="error"
        showIcon
        message={`${presentation.type} 执行失败`}
        description={statement.error || '执行失败，请查看运行日志'}
        style={{ margin: 12 }}
      />
    )
  }
  if (statement.status === 'skipped' || statement.status === 'cancelled') {
    return (
      <Alert
        type="warning"
        showIcon
        message={statement.status === 'skipped' ? '语句未执行' : '语句已停止'}
        description={statement.error || '请查看运行日志了解详细原因'}
        style={{ margin: 12 }}
      />
    )
  }

  const objectName = statementObjectName(statement.sql)
  if (presentation.kind === 'dml') {
    const affected = statement.affected_rows
    return (
      <Alert
        type="success"
        showIcon
        message={`${presentation.type} 执行成功`}
        description={affected != null && affected >= 0 ? `影响 ${affected} 行` : '数据库未返回影响行数'}
        style={{ margin: 12 }}
      />
    )
  }
  if (presentation.kind === 'ddl') {
    return (
      <Alert
        type="success"
        showIcon
        message={`${presentation.type} 执行成功`}
        description={objectName ? `对象 ${objectName} 的结构变更已完成` : '结构变更已完成'}
        style={{ margin: 12 }}
      />
    )
  }
  return (
    <Alert
      type="success"
      showIcon
      message={`${presentation.type} 执行成功`}
      description={presentation.kind === 'query' ? '查询未返回列信息' : '命令已完成'}
      style={{ margin: 12 }}
    />
  )
}
