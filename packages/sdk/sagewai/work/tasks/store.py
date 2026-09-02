# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable, project-scoped storage for Tasks with fenced appends."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from sagewai._project_scope import project_scope_key
from sagewai.db import factory
from sagewai.db.models import (
    Base,
    TaskCommandModel,
    TaskDefaultsModel,
    TaskEventModel,
    TaskFeedModel,
    TaskModel,
    TaskSpendModel,
)
from sagewai.work.tasks.events import TaskEvent
from sagewai.work.tasks.feed import FeedBus, FeedEntry
from sagewai.work.tasks.models import (
    TERMINAL_STATUSES,
    Task,
    TaskDefaults,
    TaskRecord,
    TaskStatus,
)


class StaleTaskError(RuntimeError):
    """A write lost a compare-and-set on sequence, lease epoch, or revision."""


class SpendReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: str
    project_id: str
    task_id: str
    cycle: int = Field(ge=0)
    role: str
    runtime: str
    usd_reserved: Decimal = Field(ge=0)


class SpendTotals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    usd_reserved: Decimal
    usd_actual: Decimal
    unknown: int
    reservations: int


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class TaskStore:
    """Append Task events, project them, and keep leases, receipts, spend, defaults."""

    def __init__(self, *, engine: AsyncEngine | None = None, feed_bus: FeedBus | None = None) -> None:
        self._engine = engine or factory.get_engine()
        self._feed_bus = feed_bus or FeedBus()
        self._tasks = TaskModel.__table__
        self._events = TaskEventModel.__table__
        self._feed = TaskFeedModel.__table__
        self._commands = TaskCommandModel.__table__
        self._spend = TaskSpendModel.__table__
        self._defaults = TaskDefaultsModel.__table__

    @property
    def feed_bus(self) -> FeedBus:
        return self._feed_bus

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; Alembic owns PostgreSQL schema."""
        if self._engine.dialect.name != "sqlite":
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ── time helpers (DB clock on Postgres, app clock on SQLite) ─────────

    def _now_expr(self):
        if self._engine.dialect.name == "postgresql":
            return func.now()
        return datetime.now(timezone.utc)

    def _expiry_expr(self, ttl_seconds: int):
        if self._engine.dialect.name == "postgresql":
            return func.now() + timedelta(seconds=ttl_seconds)
        return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    # ── create / append ───────────────────────────────────────────────────

    async def create(self, task: Task, *, events: Sequence[TaskEvent], record: TaskRecord) -> TaskRecord:
        """Insert definition, projection, first events, and feed entries atomically."""
        if not events or events[0].sequence != 1:
            raise ValueError("task creation requires events starting at sequence 1")
        self._validate_events(task.id, task.project_id, events, expected_sequence=1)
        if record.task_id != task.id or record.project_id != task.project_id:
            raise ValueError("record belongs to a different task")
        scope = project_scope_key(task.project_id)
        stored = record.model_copy(update={"revision": 1})
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    insert(self._tasks).values(**self._task_row(scope, task, stored))
                )
                entries = await self._insert_events_and_feed(conn, scope, events, feed_start=1)
        except IntegrityError as exc:
            raise ValueError(f"task already exists: {task.id}") from exc
        for entry in entries:
            await self._feed_bus.publish(entry)
        return stored

    async def append(
        self,
        *,
        task_id: str,
        project_id: str,
        events: Sequence[TaskEvent],
        expected_sequence: int,
        record: TaskRecord,
        lease_epoch: int | None = None,
    ) -> TaskRecord:
        """Append events at ``expected_sequence`` and replace the projection in one transaction."""
        self._validate_events(task_id, project_id, events, expected_sequence=expected_sequence)
        if record.task_id != task_id or record.project_id != project_id:
            raise ValueError("record belongs to a different task")
        scope = project_scope_key(project_id)
        try:
            async with self._engine.begin() as conn:
                current = await self._lock_row(conn, scope, task_id)
                if current is None:
                    raise KeyError(task_id)
                if lease_epoch is not None and int(current["lease_epoch"]) != lease_epoch:
                    raise StaleTaskError("lease epoch changed; another coordinator owns this task")
                last = await conn.scalar(
                    select(func.coalesce(func.max(self._events.c.sequence), 0)).where(
                        self._events.c.project_scope_key == scope, self._events.c.task_id == task_id
                    )
                )
                if int(last or 0) + 1 != expected_sequence:
                    raise StaleTaskError(
                        f"expected sequence {expected_sequence}, stream is at {last}"
                    )
                feed_last = await conn.scalar(
                    select(func.coalesce(func.max(self._feed.c.feed_sequence), 0)).where(
                        self._feed.c.project_scope_key == scope, self._feed.c.task_id == task_id
                    )
                )
                entries = await self._insert_events_and_feed(
                    conn, scope, events, feed_start=int(feed_last or 0) + 1
                )
                stored = record.model_copy(
                    update={
                        "revision": int(current["revision"]) + 1,
                        "lease_owner": current["lease_owner"],
                        "lease_epoch": int(current["lease_epoch"]),
                        "lease_expires_at": _as_utc(current["lease_expires_at"]),
                    }
                )
                await conn.execute(
                    update(self._tasks)
                    .where(self._tasks.c.project_scope_key == scope, self._tasks.c.task_id == task_id)
                    .values(**self._projection_values(stored))
                )
        except IntegrityError as exc:
            raise StaleTaskError("concurrent append won the sequence") from exc
        for entry in entries:
            await self._feed_bus.publish(entry)
        return stored

    async def append_feed(self, entries: Sequence[FeedEntry]) -> list[FeedEntry]:
        """Append feed rows from Work events or activity (ingestion path), own transaction."""
        stored: list[FeedEntry] = []
        for entry in entries:
            scope = project_scope_key(entry.project_id)
            try:
                async with self._engine.begin() as conn:
                    feed_last = await conn.scalar(
                        select(func.coalesce(func.max(self._feed.c.feed_sequence), 0)).where(
                            self._feed.c.project_scope_key == scope,
                            self._feed.c.task_id == entry.task_id,
                        )
                    )
                    sequenced = entry.model_copy(update={"feed_sequence": int(feed_last or 0) + 1})
                    await conn.execute(insert(self._feed).values(**self._feed_row(scope, sequenced)))
            except IntegrityError as exc:
                raise StaleTaskError("concurrent feed append") from exc
            await self._feed_bus.publish(sequenced)
            stored.append(sequenced)
        return stored

    # ── reads ─────────────────────────────────────────────────────────────

    async def read_events(self, task_id: str, *, project_id: str) -> list[TaskEvent]:
        scope = project_scope_key(project_id)
        query = (
            select(self._events)
            .where(self._events.c.project_scope_key == scope, self._events.c.task_id == task_id)
            .order_by(self._events.c.sequence)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [self._event_from_row(row._mapping) for row in rows]

    async def read_feed(
        self, task_id: str, *, project_id: str, after: int = 0, limit: int = 500
    ) -> list[FeedEntry]:
        scope = project_scope_key(project_id)
        query = (
            select(self._feed)
            .where(
                self._feed.c.project_scope_key == scope,
                self._feed.c.task_id == task_id,
                self._feed.c.feed_sequence > after,
            )
            .order_by(self._feed.c.feed_sequence)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [self._feed_from_row(row._mapping) for row in rows]

    async def load(self, task_id: str, *, project_id: str) -> tuple[Task, TaskRecord] | None:
        scope = project_scope_key(project_id)
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self._tasks).where(
                        self._tasks.c.project_scope_key == scope, self._tasks.c.task_id == task_id
                    )
                )
            ).first()
        if row is None:
            return None
        return Task.model_validate(row._mapping["task_json"]), self._record_from_row(row._mapping)

    async def load_record(self, task_id: str, *, project_id: str) -> TaskRecord | None:
        loaded = await self.load(task_id, project_id=project_id)
        return None if loaded is None else loaded[1]

    async def list_records(
        self,
        *,
        project_id: str,
        statuses: Sequence[TaskStatus] | None = None,
    ) -> list[TaskRecord]:
        scope = project_scope_key(project_id)
        query = select(self._tasks).where(self._tasks.c.project_scope_key == scope)
        if statuses is not None:
            query = query.where(self._tasks.c.status.in_([status.value for status in statuses]))
        query = query.order_by(self._tasks.c.created_at, self._tasks.c.task_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [self._record_from_row(row._mapping) for row in rows]

    # ── leases (Task 8) ───────────────────────────────────────────────────

    async def claim(self, task_id: str, *, project_id: str, owner: str, ttl_seconds: int) -> int | None:
        """Take the lease if free or expired; returns the new epoch, or None."""
        scope = project_scope_key(project_id)
        now = self._now_expr()
        statement = (
            update(self._tasks)
            .where(
                self._tasks.c.project_scope_key == scope,
                self._tasks.c.task_id == task_id,
                self._tasks.c.status.notin_([status.value for status in TERMINAL_STATUSES]),
                (self._tasks.c.lease_expires_at.is_(None)) | (self._tasks.c.lease_expires_at < now),
            )
            .values(
                lease_owner=owner,
                lease_epoch=self._tasks.c.lease_epoch + 1,
                lease_expires_at=self._expiry_expr(ttl_seconds),
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(statement)
            if result.rowcount != 1:
                return None
            epoch = await conn.scalar(
                select(self._tasks.c.lease_epoch).where(
                    self._tasks.c.project_scope_key == scope, self._tasks.c.task_id == task_id
                )
            )
        return int(epoch)

    async def renew(self, task_id: str, *, project_id: str, owner: str, lease_epoch: int, ttl_seconds: int) -> bool:
        scope = project_scope_key(project_id)
        statement = (
            update(self._tasks)
            .where(
                self._tasks.c.project_scope_key == scope,
                self._tasks.c.task_id == task_id,
                self._tasks.c.lease_owner == owner,
                self._tasks.c.lease_epoch == lease_epoch,
                self._tasks.c.lease_expires_at >= self._now_expr(),
            )
            .values(lease_expires_at=self._expiry_expr(ttl_seconds))
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(statement)
        return result.rowcount == 1

    async def release(self, task_id: str, *, project_id: str, owner: str, lease_epoch: int) -> bool:
        scope = project_scope_key(project_id)
        statement = (
            update(self._tasks)
            .where(
                self._tasks.c.project_scope_key == scope,
                self._tasks.c.task_id == task_id,
                self._tasks.c.lease_owner == owner,
                self._tasks.c.lease_epoch == lease_epoch,
            )
            .values(lease_owner=None, lease_expires_at=None)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(statement)
        return result.rowcount == 1

    async def expire_lease_for_tests(self, task_id: str, *, project_id: str) -> None:
        """Force the lease into the past; used by tests, never by the coordinator."""
        scope = project_scope_key(project_id)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._tasks)
                .where(self._tasks.c.project_scope_key == scope, self._tasks.c.task_id == task_id)
                .values(lease_expires_at=past)
            )

    # ── receipts, ledger, defaults (Task 9) ───────────────────────────────

    async def record_command(self, *, task_id: str, project_id: str, command_id: str, payload: dict[str, Any]) -> bool:
        raise NotImplementedError  # replaced in Task 9

    async def reserve_spend(self, reservation: SpendReservation) -> None:
        raise NotImplementedError  # replaced in Task 9

    async def settle_spend(self, reservation_id: str, *, project_id: str, usd_actual: Decimal | None) -> None:
        raise NotImplementedError  # replaced in Task 9

    async def spend_totals(self, *, task_id: str, project_id: str, cycle: int) -> SpendTotals:
        raise NotImplementedError  # replaced in Task 9

    async def get_defaults(self, *, project_id: str) -> TaskDefaults:
        raise NotImplementedError  # replaced in Task 9

    async def put_defaults(self, defaults: TaskDefaults, *, expected_revision: int) -> TaskDefaults:
        raise NotImplementedError  # replaced in Task 9

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate_events(task_id: str, project_id: str, events: Sequence[TaskEvent], *, expected_sequence: int) -> None:
        if not events:
            raise ValueError("append requires at least one event")
        for offset, event in enumerate(events):
            if event.task_id != task_id or event.project_id != project_id:
                raise ValueError("event belongs to a different task")
            if event.sequence != expected_sequence + offset:
                raise ValueError("events must be consecutive from the expected sequence")

    async def _lock_row(self, conn: AsyncConnection, scope: str, task_id: str):
        query = select(self._tasks).where(
            self._tasks.c.project_scope_key == scope, self._tasks.c.task_id == task_id
        )
        if self._engine.dialect.name == "postgresql":
            query = query.with_for_update()
        row = (await conn.execute(query)).first()
        return None if row is None else row._mapping

    async def _insert_events_and_feed(
        self, conn: AsyncConnection, scope: str, events: Sequence[TaskEvent], *, feed_start: int
    ) -> list[FeedEntry]:
        entries: list[FeedEntry] = []
        for offset, event in enumerate(events):
            values = event.model_dump(mode="python")
            values["project_scope_key"] = scope
            values["event_type"] = event.event_type.value
            await conn.execute(insert(self._events).values(**values))
            entry = FeedEntry(
                project_id=event.project_id,
                task_id=event.task_id,
                feed_sequence=feed_start + offset,
                source="task_event",
                source_id=event.id,
                event_type=event.event_type.value,
                payload_json=event.payload_json,
                created_at=event.created_at,
            )
            await conn.execute(insert(self._feed).values(**self._feed_row(scope, entry)))
            entries.append(entry)
        return entries

    @staticmethod
    def _feed_row(scope: str, entry: FeedEntry) -> dict[str, Any]:
        values = entry.model_dump(mode="python")
        values.pop("project_id")
        values["project_scope_key"] = scope
        return values

    @staticmethod
    def _projection_values(record: TaskRecord) -> dict[str, Any]:
        values = record.model_dump(mode="python")
        for key in ("task_id", "project_id", "kind", "origin", "title", "profile", "created_at"):
            values.pop(key)
        values["status"] = record.status.value
        values["board_column"] = record.board_column.value
        values["attention_owner"] = record.attention_owner.value if record.attention_owner else None
        values["budget_used"] = record.budget_used.model_dump(mode="json")
        return values

    def _task_row(self, scope: str, task: Task, record: TaskRecord) -> dict[str, Any]:
        values = self._projection_values(record)
        values.update(
            project_scope_key=scope,
            task_id=task.id,
            project_id=task.project_id,
            kind=task.kind.value,
            origin=task.origin.value,
            title=task.title,
            profile=task.profile,
            task_json=task.model_dump(mode="json"),
            created_at=record.created_at,
        )
        return values

    @staticmethod
    def _record_from_row(values) -> TaskRecord:
        data = dict(values)
        data.pop("project_scope_key")
        data.pop("task_json")
        for key in ("created_at", "updated_at", "next_run_at", "lease_expires_at"):
            data[key] = _as_utc(data[key])
        return TaskRecord.model_validate(data)

    @staticmethod
    def _event_from_row(values) -> TaskEvent:
        data = dict(values)
        data.pop("project_scope_key")
        data["created_at"] = _as_utc(data["created_at"])
        return TaskEvent.model_validate(data)

    @staticmethod
    def _feed_from_row(values) -> FeedEntry:
        data = dict(values)
        scope = data.pop("project_scope_key")
        data["project_id"] = scope.removeprefix("p:")
        data["created_at"] = _as_utc(data["created_at"])
        return FeedEntry.model_validate(data)


__all__ = ["SpendReservation", "SpendTotals", "StaleTaskError", "TaskStore"]
