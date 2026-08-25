# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable append-only storage for the generic Work domain."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.models import Base, WorkEventModel, WorkItemModel
from sagewai.work.events import WorkEvent
from sagewai.work.models import WorkRecord


def _as_utc(value: datetime) -> datetime:
    """Restore the UTC marker SQLite drops from timezone-aware timestamps."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class WorkStore:
    """Append Work events and persist their mutable current projection."""

    def __init__(self, *, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or factory.get_engine()
        self._work_items = WorkItemModel.__table__
        self._work_events = WorkEventModel.__table__

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; Alembic owns PostgreSQL schema."""
        if self._engine.dialect.name != "sqlite":
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def append_event(self, event: WorkEvent) -> None:
        """Append one immutable event; database constraints reject duplicates."""
        values = event.model_dump(mode="python")
        values["event_type"] = event.event_type.value
        async with self._engine.begin() as conn:
            projection = (
                await conn.execute(
                    select(self._work_items.c.project_id).where(
                        self._work_items.c.work_id == event.work_id
                    )
                )
            ).first()
            if projection is not None and projection.project_id != event.project_id:
                raise ValueError("work_id belongs to a different project")

            existing_event = (
                await conn.execute(
                    select(self._work_events.c.project_id)
                    .where(self._work_events.c.work_id == event.work_id)
                    .limit(1)
                )
            ).first()
            if existing_event is not None and existing_event.project_id != event.project_id:
                raise ValueError("work_id belongs to a different project")

            await conn.execute(insert(self._work_events).values(**values))

    async def read_events(
        self,
        work_id: str,
        *,
        project_id: str | None,
    ) -> list[WorkEvent]:
        """Read a project-scoped Work stream in deterministic sequence order."""
        table = self._work_events
        query = (
            select(table)
            .where(table.c.work_id == work_id, table.c.project_id == project_id)
            .order_by(table.c.sequence)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [self._event_from_row(row._mapping) for row in rows]

    async def save_work(self, record: WorkRecord) -> None:
        """Insert or replace the current projection for one WorkItem."""
        table = self._work_items
        values = record.model_dump(mode="python")
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(table.c.project_id).where(table.c.work_id == record.work_id)
                )
            ).first()
            if existing is not None and existing.project_id != record.project_id:
                raise ValueError("work_id belongs to a different project")

            existing_event = (
                await conn.execute(
                    select(self._work_events.c.project_id)
                    .where(self._work_events.c.work_id == record.work_id)
                    .limit(1)
                )
            ).first()
            if existing_event is not None and existing_event.project_id != record.project_id:
                raise ValueError("work_id belongs to a different project")

            statement = upsert(
                table,
                values,
                index_elements=["work_id"],
                set_={
                    column: values[column]
                    for column in values
                    if column not in {"work_id", "project_id", "created_at"}
                },
                dialect=self._engine.dialect.name,
            )
            await conn.execute(statement)

    async def load_work(
        self,
        work_id: str,
        *,
        project_id: str | None,
    ) -> WorkRecord | None:
        """Load one project-scoped current Work projection."""
        table = self._work_items
        query = select(table).where(
            table.c.work_id == work_id,
            table.c.project_id == project_id,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
        if row is None:
            return None
        values = dict(row._mapping)
        values["created_at"] = _as_utc(values["created_at"])
        values["updated_at"] = _as_utc(values["updated_at"])
        return WorkRecord.model_validate(values)

    @staticmethod
    def _event_from_row(values) -> WorkEvent:
        data = dict(values)
        data["created_at"] = _as_utc(data["created_at"])
        return WorkEvent.model_validate(data)
