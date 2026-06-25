# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Optional

from app.services.dolphin import ds_client
from app.services.scheduler_engine.base import SchedulerDefinitionRef, SchedulerInstanceRef, SchedulerLogRef


class DolphinSchedulerEngine:
    """DolphinScheduler 执行引擎适配器：业务层只依赖 GIDO 调度语义。"""

    name = "dolphin"

    def publish_definition(self, workflow: Any, *, db: Any = None) -> SchedulerDefinitionRef:
        project_code = ds_client.get_or_create_project()
        process_code, task_sync = ds_client.sync_workflow(workflow, db=db)
        return SchedulerDefinitionRef(
            engine=self.name,
            project_id=str(project_code),
            definition_id=str(process_code),
            task_sync=task_sync,
        )

    def online_definition(self, project_id: str, definition_id: str) -> None:
        ds_client.online_process(int(project_id), int(definition_id))

    def offline_definition(self, project_id: str, definition_id: str) -> None:
        ds_client.offline_process(int(project_id), int(definition_id))

    def set_schedule(self, project_id: str, definition_id: str, cron_expression: str) -> None:
        ds_client.set_schedule(int(project_id), int(definition_id), cron_expression)

    def pause_schedule(self, project_id: str, definition_id: str) -> int:
        return ds_client.offline_schedules(int(project_id), int(definition_id))

    def resume_schedule(self, project_id: str, definition_id: str) -> int:
        return ds_client.online_schedules(int(project_id), int(definition_id))

    def trigger(self, project_id: str, definition_id: str, *, business_date: Optional[str] = None) -> SchedulerInstanceRef:
        instance_id = ds_client.run_process(int(project_id), int(definition_id), business_date)
        return SchedulerInstanceRef(engine=self.name, instance_id=str(instance_id))

    def stop_instance(self, project_id: str, instance_id: str) -> None:
        ds_client.control_process_instance(int(project_id), int(instance_id), "STOP")

    def retry_failed_nodes(self, project_id: str, instance_id: str) -> None:
        ds_client.control_process_instance(int(project_id), int(instance_id), "START_FAILURE_TASK_PROCESS")

    def retry_task(self, project_id: str, instance_id: str, task_code: str) -> None:
        ds_client.execute_task_on_instance(int(project_id), int(instance_id), int(task_code))

    def get_task_log(self, task_instance_id: str) -> SchedulerLogRef:
        try:
            log = ds_client.get_task_log(int(task_instance_id))
        except Exception as e:
            msg = str(e)
            lowered = msg.lower()
            if "logpath is empty" in lowered or "doesn't be dispatched" in lowered or "not be dispatched" in lowered:
                return SchedulerLogRef(
                    engine=self.name,
                    status="not_dispatched",
                    message="节点尚未下发到 Worker，暂无调度日志。",
                    log="",
                )
            if "not found" in lowered or "not exist" in lowered or "不存在" in lowered:
                return SchedulerLogRef(engine=self.name, status="not_found", message="调度任务实例不存在。", log="")
            return SchedulerLogRef(engine=self.name, status="engine_error", message=f"调度日志暂不可用: {msg}", log="")
        return SchedulerLogRef(engine=self.name, status="available" if log else "log_empty", message="日志来自生产调度引擎。", log=log or "")


dolphin_scheduler_engine = DolphinSchedulerEngine()
