/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：本地文件导入入口 / API / 大文件能力不得回退。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('file import adoption / regression', () => {
  it('数据集成页提供本地文件导入入口与 FileImportDrawer', () => {
    const src = read('pages/Integration.tsx')
    expect(src).toContain('FileImportDrawer')
    expect(src).toContain('本地文件导入')
    expect(src).toContain('file-import')
    expect(src).toContain('fileImportOpen')
    expect(src).toContain("sync_mode === 'file_import'")
  })

  it('数据字典提供从本地文件建表入口', () => {
    const src = read('pages/DataMap.tsx')
    expect(src).toContain('从本地文件建表')
    expect(src).toContain('file-import')
  })

  it('FileImportDrawer 四步与大文件文案', () => {
    const src = read('components/FileImportDrawer.tsx')
    expect(src).toContain('选择文件')
    expect(src).toContain('字段与建表')
    expect(src).toContain('数据去向')
    expect(src).toContain('装载与确认')
    expect(src).toContain('3GB')
    expect(src).toContain('Stream Load')
    expect(src).toContain('断点续传')
    expect(src).toContain('取消上传')
    expect(src).toContain('describeUploadNetworkError')
    expect(src).toContain('loadFileImportSession')
  })

  it('integrationApi 暴露文件导入接口且上传关闭短超时', () => {
    const src = read('api/index.ts')
    expect(src).toContain('uploadFileImport')
    expect(src).toContain('fileImportUploadStatus')
    expect(src).toContain('abortFileImportUpload')
    expect(src).toContain('uploadFileImportResumable')
    expect(src).toContain('/integration/file-import/upload')
    expect(src).toMatch(/timeout:\s*0/)
  })

  it('resumable util 与 nginx 配置到位', () => {
    const util = read('utils/fileImportUpload.ts')
    expect(util).toContain('upload-init')
    expect(util).toContain('upload-chunk')
    expect(util).toContain('upload-complete')
    expect(util).toContain('upload-status')
    expect(util).toContain('missing_chunks')
    expect(util).toContain('localStorage')
    expect(util).toContain('MAX_ATTEMPTS')
    const nginx = readFileSync(resolve(root, '../nginx.conf'), 'utf8')
    expect(nginx).toContain('client_max_body_size 3072m')
    expect(nginx).toContain('proxy_read_timeout 3600s')
    expect(nginx).toContain('proxy_request_buffering off')
  })
})
