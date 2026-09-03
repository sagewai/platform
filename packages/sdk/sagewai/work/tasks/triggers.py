# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Turn external events into Tasks through admin-approved trigger specifications (section 8.7)."""

from __future__ import annotations

from datetime import datetime

from sagewai.work.profiles.software.github import GitHubFactory
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import TaskOrigin, TaskTriggerSpec
from sagewai.work.tasks.service import TaskService
from sagewai.work.tasks.store import TaskStore


class TriggerIntake:
    """One Task per new labelled issue, bounded by the trigger's approved authority."""

    def __init__(
        self,
        *,
        task_store: TaskStore,
        work_store: WorkStore,
        service: TaskService,
        github_factory: GitHubFactory,
    ) -> None:
        self._task_store = task_store
        self._work_store = work_store
        self._service = service
        self._github_factory = github_factory

    async def run(self, *, project_id: str, now: datetime) -> list[str]:
        created: list[str] = []
        for spec in await self._task_store.list_triggers(project_id=project_id):
            created.extend(await self._run_one(spec, now))
        return created

    async def _run_one(self, spec: TaskTriggerSpec, now: datetime) -> list[str]:
        issues = await self._github_factory(spec).list_labeled_issues(
            owner=spec.filter["owner"], repo=spec.filter["repo"], label=spec.filter["label"]
        )
        created: list[str] = []
        for issue in issues:
            bound = await self._work_store.find_work_by_source_ref(
                issue.url, project_id=spec.project_id
            )
            if bound is not None:
                continue
            command_id = f"trigger:{spec.trigger_id}:{issue.url}"
            if not await self._task_store.record_command(
                task_id=f"trigger:{spec.trigger_id}",
                project_id=spec.project_id,
                command_id=command_id,
                payload={"issue_url": issue.url},
            ):
                continue
            try:
                task, _record = await self._service.create(
                    f"{issue.title}\n\n{issue.body}",
                    project_id=spec.project_id,
                    origin=TaskOrigin.TRIGGER,
                    created_by=f"trigger:{spec.trigger_id}",
                    authority_floor=spec.authority,
                    origin_ref=spec.trigger_id,
                    source_ref=issue.url,
                    now=now,
                )
            except Exception:
                await self._task_store.delete_command(
                    task_id=f"trigger:{spec.trigger_id}",
                    project_id=spec.project_id,
                    command_id=command_id,
                )
                raise
            created.append(task.id)
        return created


__all__ = ["TriggerIntake"]
