---
name: gido-git-push-remotes
description: >-
  Push GIDO to gitee/github/github2 (never origin), and recover when GitHub SSH
  port 22 fails via proxy Fake-IP 198.18.0.72 by using ssh.github.com:443.
  Use when the user asks to push remotes, 推送远程, git push github/github2 fails
  with Connection closed, or dig github.com returns 198.18.0.x.
---

# GIDO 多远程推送

## 远程清单（硬性）

| 远程 | 操作 |
|------|------|
| `gitee` | **推** |
| `github` | **推** |
| `github2` | **推** |
| `origin` | **禁止推**（无交互凭证、已弃用） |

分支默认当前分支（如 `dev`）：`git push <remote> HEAD:<branch>`。

## 标准流程

1. `git status -sb`、确认要推的 commit（未提交先问是否 commit，勿擅自空推）
2. 依次：`git push gitee HEAD:<branch>` → `github` → `github2`
3. **禁止** `git push origin`
4. 用 `git ls-remote <remote> refs/heads/<branch>` 核对 tip 与 `HEAD` 一致

## GitHub SSH 失败：立刻走 443（不要干等/重复 22）

本机代理常把 `github.com` Fake-IP 成 **`198.18.0.x`**，**22 端口会 `Connection closed`**，HTTPS 仍可能正常。已因此空失败多次——**同一失败方式最多试 1 次，然后必须换 443**。

**判定（任一即切换）：**

- `Connection closed by 198.18.0.x port 22`
- `fatal: Could not read from remote repository` 且仅 github/github2 失败、gitee 成功
- `dig +short github.com` / `dscacheutil` 得到 `198.18.0.x`

**立刻执行（勿再死磕 `git push github`）：**

```bash
# 先确认 443 可用
ssh -o ConnectTimeout=15 -p 443 -T git@ssh.github.com

# 推 github / github2（按实际仓库路径）
GIT_SSH_COMMAND='ssh -p 443' git push git@ssh.github.com:gidocloud/gido.git HEAD:dev
GIT_SSH_COMMAND='ssh -p 443' git push git@ssh.github.com:cloud-gido/gido.git HEAD:dev

# 核对
GIT_SSH_COMMAND='ssh -p 443' git ls-remote git@ssh.github.com:gidocloud/gido.git refs/heads/dev
GIT_SSH_COMMAND='ssh -p 443' git ls-remote git@ssh.github.com:cloud-gido/gido.git refs/heads/dev
git rev-parse HEAD
```

`gitee` 仍用正常 `git push gitee`（一般不受此 Fake-IP 影响）。

## 汇报

向用户说明：哪些远程已对齐、哪些走了 443 回退；不要建议改 `origin` 凭证来「补齐」推送。
