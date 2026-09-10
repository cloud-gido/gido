/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_STREAM_JOB_STATE_FILTER,
  STREAM_JOB_FOCUS_STATES,
  STREAM_JOB_STATE_FILTER_OPTIONS,
  matchStreamJobDeployFilter,
  matchStreamJobKeyword,
  matchStreamJobStateFilter,
  matchStreamJobTypeFilter,
  streamJobDeployMode,
} from './streamJobMonitorFilters'

describe('streamJobMonitorFilters', () => {
  it('defaults to focus view of active + ready_to_deploy', () => {
    expect(DEFAULT_STREAM_JOB_STATE_FILTER).toBe('focus')
    expect(STREAM_JOB_FOCUS_STATES.has('active')).toBe(true)
    expect(STREAM_JOB_FOCUS_STATES.has('ready_to_deploy')).toBe(true)
    expect(STREAM_JOB_FOCUS_STATES.has('stopped')).toBe(false)
    expect(STREAM_JOB_FOCUS_STATES.has('needs_attention')).toBe(false)
    expect(STREAM_JOB_STATE_FILTER_OPTIONS[0].value).toBe('focus')
  })

  it('focus filter keeps running and pending deploy, hides stopped and failures', () => {
    expect(matchStreamJobStateFilter('active', 'focus')).toBe(true)
    expect(matchStreamJobStateFilter('ready_to_deploy', 'focus')).toBe(true)
    expect(matchStreamJobStateFilter('stopped', 'focus')).toBe(false)
    expect(matchStreamJobStateFilter('needs_attention', 'focus')).toBe(false)
    expect(matchStreamJobStateFilter('draft', 'focus')).toBe(false)
    expect(matchStreamJobStateFilter('terminal', 'focus')).toBe(false)
  })

  it('exact state filter and cleared filter behave as expected', () => {
    expect(matchStreamJobStateFilter('stopped', 'stopped')).toBe(true)
    expect(matchStreamJobStateFilter('active', 'stopped')).toBe(false)
    expect(matchStreamJobStateFilter('needs_attention', undefined)).toBe(true)
    expect(matchStreamJobStateFilter('stopped', null)).toBe(true)
  })

  it('matches keyword across name / CR / submitter', () => {
    const row = {
      name: 'flink-ods-orders',
      flink_operator_deployment_name: 'gido-sql-1-19',
      last_submitted_by_username: 'admin',
    }
    expect(matchStreamJobKeyword(row, 'ods')).toBe(true)
    expect(matchStreamJobKeyword(row, 'gido-sql-1-19')).toBe(true)
    expect(matchStreamJobKeyword(row, 'admin')).toBe(true)
    expect(matchStreamJobKeyword(row, 'nope')).toBe(false)
    expect(matchStreamJobKeyword(row, '  ')).toBe(true)
  })

  it('classifies deploy mode for Operator vs Session', () => {
    expect(streamJobDeployMode({
      job_type: 'SQL',
      flink_sql_submit_mode: 'flink_operator',
    })).toBe('operator')
    expect(streamJobDeployMode({
      job_type: 'JAR',
    })).toBe('operator')
    expect(streamJobDeployMode({
      job_type: 'SQL',
      flink_sql_submit_mode: 'session',
    })).toBe('session')
    expect(matchStreamJobDeployFilter({ job_type: 'SQL', flink_sql_submit_mode: 'flink_operator' }, 'operator')).toBe(true)
    expect(matchStreamJobDeployFilter({ job_type: 'SQL', flink_sql_submit_mode: 'session' }, 'operator')).toBe(false)
    expect(matchStreamJobTypeFilter('SQL', 'SQL')).toBe(true)
    expect(matchStreamJobTypeFilter('JAR', 'SQL')).toBe(false)
    expect(matchStreamJobTypeFilter('JAR', undefined)).toBe(true)
  })
})
