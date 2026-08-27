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
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    PendingAttention,
    PendingAttentionKind,
    WorkRecord,
)


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

    async def pending_attention(
        self,
        *,
        project_id: str | None,
    ) -> tuple[PendingAttention, ...]:
        """List unresolved gates, blocks, and control degradations for a project."""
        work_query = (
            select(self._work_items)
            .where(self._work_items.c.project_id == project_id)
            .order_by(self._work_items.c.created_at, self._work_items.c.work_id)
        )
        event_query = (
            select(self._work_events)
            .where(self._work_events.c.project_id == project_id)
            .order_by(self._work_events.c.work_id, self._work_events.c.sequence)
        )
        async with self._engine.connect() as conn:
            work_rows = (await conn.execute(work_query)).all()
            event_rows = (await conn.execute(event_query)).all()

        events_by_work: dict[str, list[WorkEvent]] = {}
        for row in event_rows:
            event = self._event_from_row(row._mapping)
            events_by_work.setdefault(event.work_id, []).append(event)

        pending: list[PendingAttention] = []
        for row in work_rows:
            projection = row._mapping
            work_id = str(projection["work_id"])
            source_ref = projection["source_ref"]
            events = events_by_work.get(work_id, [])

            decided_gate_ids = {
                str(event.payload_json["gate_id"])
                for event in events
                if event.event_type is WorkEventType.GATE_DECIDED
            }
            for event in events:
                if event.event_type is not WorkEventType.GATE_REQUESTED:
                    continue
                gate_id = str(event.payload_json["gate_id"])
                if gate_id in decided_gate_ids:
                    continue
                pending.append(
                    PendingAttention(
                        attention_id=gate_id,
                        project_id=project_id,
                        work_id=work_id,
                        kind=PendingAttentionKind.GATE_REQUESTED,
                        source_ref=source_ref,
                        summary=str(event.payload_json["question"]),
                        evidence_refs=tuple(
                            str(ref) for ref in event.payload_json.get("evidence_refs", ())
                        ),
                        created_at=event.created_at,
                    )
                )

            if projection["status"] == "WORK_BLOCKED":
                blocked = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type is WorkEventType.WORK_BLOCKED
                    ),
                    None,
                )
                if blocked is not None:
                    summary = blocked.payload_json.get("decision_request")
                    if summary is None:
                        summary = blocked.payload_json["reason"]
                    pending.append(
                        PendingAttention(
                            attention_id=blocked.id,
                            project_id=project_id,
                            work_id=work_id,
                            kind=PendingAttentionKind.WORK_BLOCKED,
                            source_ref=source_ref,
                            summary=str(summary),
                            evidence_refs=tuple(
                                str(ref)
                                for ref in blocked.payload_json.get(
                                    "evidence_refs",
                                    (),
                                )
                            ),
                            created_at=blocked.created_at,
                        )
                    )

            degraded: dict[str, WorkEvent] = {}
            for event in events:
                if event.event_type is WorkEventType.CONTROL_DEGRADED:
                    for precondition_id in event.payload_json["failed_preconditions"]:
                        degraded[str(precondition_id)] = event
                elif event.event_type is WorkEventType.CONTROL_RESTORED:
                    for precondition_id in event.payload_json["precondition_ids"]:
                        degraded.pop(str(precondition_id), None)
            for precondition_id, event in degraded.items():
                pending.append(
                    PendingAttention(
                        attention_id=precondition_id,
                        project_id=project_id,
                        work_id=work_id,
                        kind=PendingAttentionKind.CONTROL_DEGRADED,
                        source_ref=source_ref,
                        summary=str(
                            event.payload_json.get("details")
                            or f"Control precondition failed: {precondition_id}."
                        ),
                        evidence_refs=tuple(
                            str(ref) for ref in event.payload_json.get("evidence_refs", ())
                        ),
                        created_at=event.created_at,
                    )
                )

        pending.sort(
            key=lambda item: (
                item.created_at,
                item.work_id,
                item.kind.value,
                item.attention_id,
            )
        )
        return tuple(pending)

    @staticmethod
    def _event_from_row(values) -> WorkEvent:
        data = dict(values)
        data["created_at"] = _as_utc(data["created_at"])
        return WorkEvent.model_validate(data)
