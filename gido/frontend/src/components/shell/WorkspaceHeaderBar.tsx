/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Button, Select, Space, Tooltip } from 'antd'
import {
  FolderAddOutlined, GlobalOutlined, PartitionOutlined, SettingOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { R } from '../../routes'
import { can, isPlatformAdmin, P } from '../../perm'
import ProductSwitcher from '../ProductSwitcher'
import type { ProductId } from '../../routes'
import UserAccountMenu from '../UserAccountMenu'

type Props = {
  product: ProductId
  user: any
  currentWorkspace: any
  workspaces: any[]
  wsLabel: (w: any) => string
  setCurrentWorkspace: (w: any) => void
  openTzModal: () => void
  onCreateWorkspace: () => void
  showWorkspaceSettings?: boolean
}

export default function WorkspaceHeaderBar({
  product,
  user,
  currentWorkspace,
  workspaces,
  wsLabel,
  setCurrentWorkspace,
  openTzModal,
  onCreateWorkspace,
  showWorkspaceSettings = false,
}: Props) {
  const navigate = useNavigate()

  return (
    <div className="dw-header-inner">
      <div className="dw-header-left">
        <ProductSwitcher active={product} />
        <span className="dw-header-toolbar-divider" aria-hidden />
        <Select
          className="dw-header-workspace-select"
          value={currentWorkspace?.id}
          onChange={id => setCurrentWorkspace(workspaces.find(w => w.id === id))}
          options={workspaces.map((w: any) => ({ label: wsLabel(w), value: w.id }))}
          style={{ width: 220 }}
          placeholder="工作空间"
          variant="borderless"
        />
        {isPlatformAdmin(user) && (
          <Tooltip title="仅平台管理员可新建工作空间">
            <Button type="text" size="small" icon={<FolderAddOutlined />} onClick={onCreateWorkspace} className="dw-link-quiet">
              新建空间
            </Button>
          </Tooltip>
        )}
        <Button type="text" size="small" icon={<GlobalOutlined />} onClick={openTzModal} className="dw-link-quiet">
          {currentWorkspace?.timezone || 'Asia/Shanghai'}
        </Button>
        {showWorkspaceSettings && currentWorkspace?.my_role === 'admin' && (
          <Button
            type="text"
            size="small"
            icon={<PartitionOutlined />}
            className="dw-link-quiet"
            onClick={() => navigate(R.batch.workspaceSettings)}
          >
            空间设置
          </Button>
        )}
        {(can(user, P.SYSTEM_ROLE_READ)
          || can(user, P.SYSTEM_INTEGRATION_READ)
          || isPlatformAdmin(user)
          || currentWorkspace?.my_role === 'admin') && (
          <Button type="text" size="small" icon={<SettingOutlined />} onClick={() => navigate(R.batch.admin)} className="dw-link-quiet">
            系统管理
          </Button>
        )}
      </div>
      <UserAccountMenu />
    </div>
  )
}
