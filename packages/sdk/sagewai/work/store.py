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

from sagewai._project_scope import project_scope_key
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.models import Base, WorkEventModel, WorkItemModel
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_degradations,
)
from sagewai.work.knowledge.control_failure import control_failure_finding
from sagewai.work.knowledge.store import insert_knowledge_item
from sagewai.work.metrics import WorkMetrics, derive_work_metrics
from sagewai.work.models import (
    ExternalOutcomeIncident,
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

        await self.append_events((event,))

    async def append_events(self, events: tuple[WorkEvent, ...]) -> None:
        """Append related immutable events in one database transaction."""

        prepared = []
        finding_errors: list[ValueError] = []
        for event in events:
            if event.event_type is WorkEventType.EXTERNAL_OUTCOME_RECORDED:
                if set(event.payload_json) != {"incident"}:
                    raise ValueError("external outcome event must contain exactly one incident")
                ExternalOutcomeIncident.model_validate(event.payload_json["incident"])
            event_values = event.model_dump(mode="python")
            event_values["project_scope_key"] = project_scope_key(event.project_id)
            event_values["event_type"] = event.event_type.value
            finding = None
            if event.event_type is WorkEventType.CONTROL_DEGRADED:
                try:
                    finding = control_failure_finding(event)
                except ValueError as exc:
                    finding_errors.append(exc)
            prepared.append((event_values, finding))

        async with self._engine.begin() as conn:
            for event_values, finding in prepared:
                await conn.execute(insert(self._work_events).values(**event_values))
                if finding is not None:
                    await insert_knowledge_item(
                        conn,
                        finding,
                        dialect_name=self._engine.dialect.name,
                    )
        if finding_errors:
            raise finding_errors[0]

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
            .where(
                table.c.project_scope_key == project_scope_key(project_id),
                table.c.work_id == work_id,
            )
            .order_by(table.c.sequence)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [self._event_from_row(row._mapping) for row in rows]

    async def metrics(
        self,
        *,
        project_id: str | None,
        work_id: str | None = None,
        profile: str | None = None,
        runtime: str | None = None,
    ) -> WorkMetrics:
        """Derive project-, Work-, profile-, or runtime-scoped event metrics."""

        table = self._work_events
        filters = [table.c.project_scope_key == project_scope_key(project_id)]
        if work_id is not None:
            filters.append(table.c.work_id == work_id)
        query = select(table).where(*filters).order_by(table.c.work_id, table.c.sequence)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return derive_work_metrics(
            (self._event_from_row(row._mapping) for row in rows),
            project_id=project_id,
            work_id=work_id,
            profile=profile,
            runtime=runtime,
        )

    async def save_work(self, record: WorkRecord) -> None:
        """Insert or replace the current projection for one WorkItem."""
        table = self._work_items
        values = record.model_dump(mode="python")
        values["project_scope_key"] = project_scope_key(record.project_id)
        async with self._engine.begin() as conn:
            statement = upsert(
                table,
                values,
                index_elements=["project_scope_key", "work_id"],
                set_={
                    column: values[column]
                    for column in values
                    if column not in {"project_scope_key", "work_id", "project_id", "created_at"}
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
            table.c.project_scope_key == project_scope_key(project_id),
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
        if row is None:
            return None
        values = dict(row._mapping)
        values.pop("project_scope_key")
        values["created_at"] = _as_utc(values["created_at"])
        values["updated_at"] = _as_utc(values["updated_at"])
        return WorkRecord.model_validate(values)

    async def list_work(
        self,
        *,
        project_id: str | None,
        active_only: bool = False,
    ) -> list[WorkRecord]:
        """List exact-project Work projections in deterministic creation order."""
        table = self._work_items
        query = select(table).where(table.c.project_scope_key == project_scope_key(project_id))
        if active_only:
            query = query.where(table.c.status != "COMPLETE")
        query = query.order_by(table.c.created_at, table.c.work_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()

        records: list[WorkRecord] = []
        for row in rows:
            values = dict(row._mapping)
            values.pop("project_scope_key")
            values["created_at"] = _as_utc(values["created_at"])
            values["updated_at"] = _as_utc(values["updated_at"])
            records.append(WorkRecord.model_validate(values))
        return records

    async def find_work_by_source_ref(
        self,
        source_ref: str,
        *,
        project_id: str | None,
    ) -> WorkRecord | None:
        """Find the canonical project-scoped Work projection for one source."""
        table = self._work_items
        query = (
            select(table)
            .where(
                table.c.source_ref == source_ref,
                table.c.project_scope_key == project_scope_key(project_id),
            )
            .order_by(table.c.created_at, table.c.work_id)
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
        if row is None:
            return None
        values = dict(row._mapping)
        values.pop("project_scope_key")
        values["created_at"] = _as_utc(values["created_at"])
        values["updated_at"] = _as_utc(values["updated_at"])
        return WorkRecord.model_validate(values)

    async def pending_attention(
        self,
        *,
        project_id: str | None,
    ) -> tuple[PendingAttention, ...]:
        """List the four canonical unresolved attention categories for a project."""
        work_query = (
            select(self._work_items)
            .where(self._work_items.c.project_scope_key == project_scope_key(project_id))
            .order_by(self._work_items.c.created_at, self._work_items.c.work_id)
        )
        event_query = (
            select(self._work_events)
            .where(self._work_events.c.project_scope_key == project_scope_key(project_id))
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

            incident_triggers: dict[str, WorkEvent] = {}
            incident_updates: dict[str, list[ExternalOutcomeIncident]] = {}
            incident_evidence: dict[str, list[str]] = {}

            for event in events:
                if event.event_type is not WorkEventType.EXTERNAL_OUTCOME_RECORDED:
                    continue
                incident = ExternalOutcomeIncident.model_validate(
                    event.payload_json["incident"]
                )
                incident_triggers.setdefault(incident.incident_id, event)
                incident_updates.setdefault(incident.incident_id, []).append(incident)
                refs = incident_evidence.setdefault(incident.incident_id, [])
                for ref in incident.evidence_refs:
                    if ref not in refs and len(refs) < 32:
                        refs.append(ref)

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

            degraded = active_control_degradations(events)
            active_control_event_ids = {event.id for event in degraded.values()}
            incident_control_event_ids: set[str] = set()
            if projection["status"] != "COMPLETE":
                for incident_id, trigger in incident_triggers.items():
                    updates = incident_updates[incident_id]
                    active_critical = [
                        incident
                        for incident in updates
                        if incident.severity == "critical"
                        and bool(
                            set(incident.active_control_event_ids)
                            & active_control_event_ids
                        )
                    ]
                    if active_critical:
                        incident = active_critical[-1]
                        severity = "critical"
                        incident_control_event_ids.update(
                            control_event_id
                            for update in active_critical
                            for control_event_id in update.active_control_event_ids
                            if control_event_id in active_control_event_ids
                        )
                    else:
                        incident = next(
                            (
                                update
                                for update in reversed(updates)
                                if update.severity == "high"
                            ),
                            updates[-1],
                        )
                        severity = "high"
                    pending.append(
                        PendingAttention(
                            attention_id=incident_id,
                            project_id=project_id,
                            work_id=work_id,
                            kind=PendingAttentionKind.EXTERNAL_OUTCOME_INCIDENT,
                            source_ref=source_ref,
                            summary=f"{severity.upper()}: {incident.summary}",
                            severity=severity,
                            evidence_refs=tuple(incident_evidence[incident_id]),
                            created_at=trigger.created_at,
                        )
                    )

            for precondition_id, event in degraded.items():
                if event.id in incident_control_event_ids:
                    continue
                pending.append(
                    PendingAttention(
                        attention_id=precondition_id,
                        project_id=project_id,
                        work_id=work_id,
                        kind=PendingAttentionKind.CONTROL_DEGRADED,
                        source_ref=source_ref,
                        summary=str(event.payload_json.get("details") or precondition_id),
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
        data.pop("project_scope_key")
        data["created_at"] = _as_utc(data["created_at"])
        return WorkEvent.model_validate(data)
