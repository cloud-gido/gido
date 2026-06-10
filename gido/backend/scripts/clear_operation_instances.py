# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env python3
"""
清空运维中心相关数据：工作流运行实例（dw_workflow_instances）与节点运行实例（dw_node_instances）。

不删除：工作流定义、任务节点、Dolphin 上的流程与历史（仅清 GIDO 库内运维展示数据）。

用法：
  python scripts/clear_operation_instances.py --yes                    # 清空全部工作区
  python scripts/clear_operation_instances.py --workspace-id 1 --yes # 仅某一工作区
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

def main() -> int:
    from app.models import rbac_models  # noqa: F401
    from app.core.database import SessionLocal
    from app.models.workspace import NodeInstance, TaskNode, Workflow, WorkflowInstance

    p = argparse.ArgumentParser(description="清空运维中心实例表（可选按工作区）")
    p.add_argument("--workspace-id", type=int, default=None, help="仅清空该工作区下的实例与该区试跑节点行")
    p.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认（自动化/容器内使用）",
    )
    args = p.parse_args()

    if args.workspace_id is not None:
        scope = f"工作区 workspace_id={args.workspace_id}"
    else:
        scope = "全部工作区"

    if not args.yes:
        print(f"即将清空运维实例数据：{scope}")
        if input("输入大写 YES 确认: ").strip() != "YES":
            print("已取消")
            return 1

    db = SessionLocal()
    try:
        if args.workspace_id is not None:
            wi_ids = [
                r[0]
                for r in db.query(WorkflowInstance.id)
                .join(Workflow, Workflow.id == WorkflowInstance.workflow_id)
                .filter(Workflow.workspace_id == args.workspace_id)
                .all()
            ]
            n_linked = 0
            if wi_ids:
                n_linked = (
                    db.query(NodeInstance)
                    .filter(NodeInstance.workflow_instance_id.in_(wi_ids))
                    .delete(synchronize_session=False)
                )
                db.query(WorkflowInstance).filter(WorkflowInstance.id.in_(wi_ids)).delete(synchronize_session=False)
            node_ids_subq = db.query(TaskNode.id).filter(TaskNode.workspace_id == args.workspace_id)
            n_orphan = (
                db.query(NodeInstance)
                .filter(
                    NodeInstance.workflow_instance_id.is_(None),
                    NodeInstance.node_id.in_(node_ids_subq),
                )
                .delete(synchronize_session=False)
            )
            msg = (
                f"已删除工作流实例 {len(wi_ids)} 条，节点实例（挂工作流）{n_linked} 条，"
                f"节点实例（仅试跑）{n_orphan} 条。"
            )
        else:
            n_ni = db.query(NodeInstance).delete(synchronize_session=False)
            n_wi = db.query(WorkflowInstance).delete(synchronize_session=False)
            msg = f"已删除全部节点实例 {n_ni} 条，工作流实例 {n_wi} 条。"

        db.commit()
        print(msg)
        print("完成。Dolphin 上的运行历史不受影响；需要时可在运维页「同步 Dolphin」重新写入库。")
        return 0
    except Exception as e:
        db.rollback()
        print(f"失败: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
