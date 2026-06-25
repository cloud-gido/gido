# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class SchedulerDefinitionRef:
    engine: str
    project_id: str
    definition_id: str
    task_sync: List[Dict[str, Any]]


@dataclass(frozen=True)
class SchedulerInstanceRef:
    engine: str
    instance_id: str


@dataclass(frozen=True)
class SchedulerLogRef:
    engine: str
    status: str
    message: str
    log: str


class SchedulerEngine(Protocol):
    name: str

    def publish_definition(self, workflow: Any, *, db: Any = None) -> SchedulerDefinitionRef:
        ...

    def online_definition(self, project_id: str, definition_id: str) -> None:
        ...

    def offline_definition(self, project_id: str, definition_id: str) -> None:
        ...

    def set_schedule(self, project_id: str, definition_id: str, cron_expression: str) -> None:
        ...

    def pause_schedule(self, project_id: str, definition_id: str) -> int:
        ...

    def resume_schedule(self, project_id: str, definition_id: str) -> int:
        ...

    def trigger(self, project_id: str, definition_id: str, *, business_date: Optional[str] = None) -> SchedulerInstanceRef:
        ...

    def stop_instance(self, project_id: str, instance_id: str) -> None:
        ...

    def retry_failed_nodes(self, project_id: str, instance_id: str) -> None:
        ...

    def retry_task(self, project_id: str, instance_id: str, task_code: str) -> None:
        ...

    def get_task_log(self, task_instance_id: str) -> SchedulerLogRef:
        ...
