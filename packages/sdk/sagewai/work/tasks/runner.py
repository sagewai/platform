# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The coordinator's cadence: claim, heartbeat, drive, release (spec section 8.2)."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from typing import Protocol

from sagewai.work.tasks.models import TaskRecord, TaskStatus
from sagewai.work.tasks.store import TaskStore

logger = logging.getLogger("sagewai.work.tasks")

_ACTIVE = (TaskStatus.PLANNING, TaskStatus.EXECUTING, TaskStatus.ASSESSING)
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_TASKS = 2
LEASE_TTL_SECONDS = 90
HEARTBEAT_SECONDS = 30.0


class TaskDriver(Protocol):
    async def drive(self, record: TaskRecord, *, lease_epoch: int) -> TaskRecord: ...


class ProjectSweeper(Protocol):
    """Per-project work the tick runs before it claims anything (section 8.2).

    PR4a registers two: ``TriggerIntake`` and ``ClarificationDeadlines``.
    """

    async def run(self, *, project_id: str, now: datetime) -> object: ...


class TaskCoordinatorRunner:
    """Owns the loop; the deciding and executing live in the driver."""

    def __init__(
        self,
        *,
        task_store: TaskStore,
        driver: TaskDriver,
        list_project_ids: Callable[[], Awaitable[Sequence[str]]],
        sweepers: Sequence[ProjectSweeper] = (),
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        max_tasks: int = DEFAULT_MAX_TASKS,
        owner: str | None = None,
        lease_ttl_seconds: int = LEASE_TTL_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._task_store = task_store
        self._driver = driver
        self._list_project_ids = list_project_ids
        self._sweepers = tuple(sweepers)
        self._interval = interval_seconds
        self._max_tasks = max_tasks
        self._owner = owner or f"coordinator-{uuid.uuid4().hex[:8]}"
        self._lease_ttl = lease_ttl_seconds
        self._heartbeat = heartbeat_seconds
        self._task: asyncio.Task | None = None

    async def tick(self) -> int:
        """Run the sweepers and drive up to max_tasks claimed Tasks per project; never raises."""
        driven = 0
        now = datetime.now(timezone.utc)
        for project_id in await self._list_project_ids():
            for sweeper in self._sweepers:
                try:
                    await sweeper.run(project_id=project_id, now=now)
                except Exception:
                    logger.exception(
                        "project sweeper failed",
                        extra={"project": project_id, "sweeper": type(sweeper).__name__},
                    )
            claimed: list[tuple[TaskRecord, int]] = []
            for record in await self._claimable(project_id, now):
                if len(claimed) == self._max_tasks:
                    break
                try:
                    epoch = await self._task_store.claim(
                        record.task_id,
                        project_id=record.project_id,
                        owner=self._owner,
                        ttl_seconds=self._lease_ttl,
                    )
                except Exception:
                    logger.exception(
                        "task claim failed", extra={"project": project_id, "task": record.task_id}
                    )
                    break
                if epoch is not None:
                    claimed.append((record, epoch))
            results = await asyncio.gather(
                *(self._drive(record, epoch) for record, epoch in claimed),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error("task drive failed", exc_info=result)
                else:
                    driven += result
        return driven

    async def _claimable(self, project_id: str, now: datetime) -> list[TaskRecord]:
        active = await self._task_store.list_records(project_id=project_id, statuses=_ACTIVE)
        due = await self._task_store.list_due(project_id=project_id, now=now)
        seen: set[str] = set()
        records: list[TaskRecord] = []
        for record in [*active, *due]:
            if record.task_id in seen:
                continue
            seen.add(record.task_id)
            records.append(record)
        return records

    async def _drive(self, record: TaskRecord, epoch: int) -> int:
        beat = asyncio.ensure_future(self._renew(record, epoch))
        try:
            await self._driver.drive(record, lease_epoch=epoch)
        finally:
            beat.cancel()
            with suppress(asyncio.CancelledError):
                await beat
            await self._task_store.release(
                record.task_id,
                project_id=record.project_id,
                owner=self._owner,
                lease_epoch=epoch,
            )
        return 1

    async def _renew(self, record: TaskRecord, epoch: int) -> None:
        while True:
            await asyncio.sleep(self._heartbeat)
            try:
                await self._task_store.renew(
                    record.task_id,
                    project_id=record.project_id,
                    owner=self._owner,
                    lease_epoch=epoch,
                    ttl_seconds=self._lease_ttl,
                )
            except Exception:
                logger.exception(
                    "task lease renew failed",
                    extra={"project": record.project_id, "task": record.task_id},
                )

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("coordinator tick failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def interval_from_env() -> float:
    return float(os.getenv("SAGEWAI_COORDINATOR_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))


def max_tasks_from_env() -> int:
    return int(os.getenv("SAGEWAI_COORDINATOR_MAX_TASKS", str(DEFAULT_MAX_TASKS)))


__all__ = [
    "ProjectSweeper",
    "TaskCoordinatorRunner",
    "TaskDriver",
    "interval_from_env",
    "max_tasks_from_env",
]
