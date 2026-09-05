# GIDO RBAC 契约

多租户权限为**双门禁**：平台能力包 × 工作空间成员角色。日常 UI 只突出当前空间身份。

## 两层模型

| 层 | 存储 | 作用 |
|----|------|------|
| 平台角色 | `User.role_id` → `dw_roles` + 权限码 | 跨空间能力：`system:*`、`gido:*:read/write/run` |
| 空间成员 | `WorkspaceMember.role` = `admin` / `developer` / `viewer` | 租户隔离与业务地板 |
| 负责人 | `Workspace.owner_id` | 归属标记，鉴权视同空间 `admin` |
| `is_admin` | 用户列 | **兼容缓存**，由 `super_admin` / `platform_admin` 同步，勿当独立开关 |

## 双门禁（权威实现）

后端：`app/services/rbac.py` → `assert_workspace_data_capability`

1. 平台管理员 **或** 空间 admin/owner：只需能访问该空间。
2. 否则：成员角色 ≥ `min_member_role`，**且**具备任一所列平台权限码。

前端：`perm.ts` 的 `can()` + Batch `workspaceMenuPolicy` / Stream `streamMenuPolicy` / Service `serviceMenuPolicy`。

## 体验约定

- 顶栏空间切换器、账号副标题：**仅本空间成员角色**。
- 平台角色：账号菜单「平台权限」、用户管理；勿与空间角色并排当第二种日常身份。
- `workspace_steward` 展示名：**数据源管家**（平台能力包），不是空间成员「空间管理员」。

## 产品门禁摘要

| 产品 | 空间地板 | 平台权限 |
|------|----------|----------|
| Batch 探查/字典 | viewer+ | probe/datamap read |
| Batch Studio/工作流/集成等 | developer+ | 对应 `gido:batch:*` |
| Stream 全产品 | developer+（viewer 不可进） | `gido:stream:*` |
| Serve | 见 `serviceMenuPolicy` | `gido:service:*` |
| 平台集成 / scheduler 运维面 | — | `system:integration:*` 或平台管理员（`*`） |
| 告警列表 | 空间成员即可 | 配置需空间 admin |

## 关键文件

- 权限码：`app/core/perm_codes.py` ↔ `frontend/src/perm.ts`
- 平台鉴权：`app/core/access.py`
- 种子角色：`app/services/rbac_seed.py`
- 管理 UI：`frontend/src/pages/SystemRbac.tsx`
