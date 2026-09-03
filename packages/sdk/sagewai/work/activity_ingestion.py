# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Persist operator activity and mirror it into the owning Task's feed."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from itertools import groupby

from sagewai._project_scope import project_scope_key
from sagewai.work.activity import OperatorActivity, WorkActivityStore
from sagewai.work.store import WorkStore
from sagewai.work.tasks.feed import FeedEntry
from sagewai.work.tasks.store import TaskStore

logger = logging.getLogger(__name__)


class BatchingActivitySink:
    """Sync ``emit`` that batches into an async flush every ``interval`` seconds or ``max_batch`` items."""

    def __init__(
        self,
        flush: Callable[[list[OperatorActivity]], Awaitable[None]],
        *,
        max_batch: int = 50,
        interval: float = 1.0,
    ) -> None:
        self._flush = flush
        self._max_batch = max_batch
        self._interval = interval
        self._buffer: list[OperatorActivity] = []
        self._timer: asyncio.TimerHandle | None = None
        self._pending: set[asyncio.Task[None]] = set()

    def emit(self, activity: OperatorActivity) -> None:
        self._buffer.append(activity)
        if len(self._buffer) >= self._max_batch:
            self._schedule_flush()
        elif self._timer is None:
            self._timer = asyncio.get_running_loop().call_later(self._interval, self._schedule_flush)

    def _schedule_flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        task = asyncio.get_running_loop().create_task(self._run_flush(batch))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _run_flush(self, batch: list[OperatorActivity]) -> None:
        try:
            await self._flush(batch)
        except Exception:
            logger.exception("activity flush failed")

    async def close(self) -> None:
        self._schedule_flush()
        if self._pending:
            await asyncio.gather(*self._pending)


class ActivityIngestion:
    def __init__(
        self,
        *,
        work_store: WorkStore,
        task_store: TaskStore,
        activity_store: WorkActivityStore,
    ) -> None:
        self._work_store = work_store
        self._task_store = task_store
        self._activity_store = activity_store

    async def ingest(self, activities: Sequence[OperatorActivity]) -> list[FeedEntry]:
        inserted = await self._activity_store.append(activities)
        entries: list[FeedEntry] = []
        for (project_id, work_id), group in groupby(
            sorted(
                inserted,
                key=lambda activity: (
                    project_scope_key(activity.project_id),
                    activity.work_id,
                    activity.run_id,
                    activity.sequence,
                ),
            ),
            key=lambda activity: (activity.project_id, activity.work_id),
        ):
            items = list(group)
            record = await self._work_store.load_work(work_id, project_id=project_id)
            task_id = record.profile_context.get("task_id") if record is not None else None
            if task_id is None:
                continue
            entries.extend(
                FeedEntry(
                    project_id=item.project_id,
                    task_id=task_id,
                    feed_sequence=1,
                    source="activity",
                    source_id=f"{item.run_id}:{item.sequence}",
                    event_type=f"activity.{item.kind}",
                    payload_json=item.model_dump(mode="json"),
                    created_at=item.at,
                )
                for item in items
            )
        return await self._task_store.append_feed(entries) if entries else []

    def sink(self) -> BatchingActivitySink:
        return BatchingActivitySink(self.ingest)


__all__ = ["ActivityIngestion", "BatchingActivitySink"]
