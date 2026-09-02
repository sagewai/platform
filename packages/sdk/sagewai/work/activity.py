# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Operator activity: the live, bounded record of what a runtime did during one stage run."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai._project_scope import project_scope_key
from sagewai.db.models import Base, WorkActivityModel

ACTIVITY_ROW_CAP = 5000
ACTIVITY_LOG_MAX_BYTES = 4 * 1024 * 1024
SUMMARY_MAX = 2000
DETAIL_MAX = 8192

ActivitySource = Literal["codex", "claude", "harness", "verifier", "coordinator"]
ActivityKind = Literal[
    "message", "reasoning", "tool_call", "tool_result", "file_change", "command", "error", "usage", "raw"
]


class OperatorActivity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str
    run_id: str
    sequence: int = Field(ge=1)
    at: datetime
    source: ActivitySource
    kind: ActivityKind
    summary: str
    detail: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    @field_validator("summary")
    @classmethod
    def _bound_summary(cls, value: str) -> str:
        return value[:SUMMARY_MAX]

    @field_validator("detail")
    @classmethod
    def _bound_detail(cls, value: str | None) -> str | None:
        return None if value is None else value[:DETAIL_MAX]


class ActivitySink(Protocol):
    def emit(self, activity: OperatorActivity) -> None: ...


class ListActivitySink:
    def __init__(self) -> None:
        self.items: list[OperatorActivity] = []

    def emit(self, activity: OperatorActivity) -> None:
        self.items.append(activity)


def activity_redactor(values: Mapping[str, str]) -> Callable[[OperatorActivity], OperatorActivity]:
    """Replace every non-empty scoped credential value with its redaction marker, longest first."""
    replacements = sorted(
        ((value, f"[REDACTED:{name}]") for name, value in values.items() if value),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def _scrub(text: str) -> str:
        for value, marker in replacements:
            text = text.replace(value, marker)
        return text

    def _redact(activity: OperatorActivity) -> OperatorActivity:
        if not replacements:
            return activity
        return activity.model_copy(
            update={
                "summary": _scrub(activity.summary)[:SUMMARY_MAX],
                "detail": None if activity.detail is None else _scrub(activity.detail)[:DETAIL_MAX],
            }
        )

    return _redact


class WorkActivityStore:
    """Durable per-run activity, capped at ``ACTIVITY_ROW_CAP`` rows with a final truncation marker."""

    def __init__(self, *, engine: AsyncEngine) -> None:
        self._engine = engine
        self._table = WorkActivityModel.__table__

    async def init(self) -> None:
        if self._engine.dialect.name != "sqlite":
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[self._table])

    async def append(self, activities: Sequence[OperatorActivity]) -> int:
        """Insert rows idempotently by sequence; rows beyond the cap collapse into one marker."""
        if not activities:
            return 0
        rows = []
        marker_written = False
        for activity in activities:
            if activity.sequence < ACTIVITY_ROW_CAP:
                rows.append(self._row(activity))
            elif not marker_written:
                rows.append(
                    self._row(
                        activity.model_copy(
                            update={"sequence": ACTIVITY_ROW_CAP, "kind": "raw", "summary": "truncated", "detail": None}
                        )
                    )
                )
                marker_written = True
        statement = (
            pg_insert(self._table) if self._engine.dialect.name == "postgresql" else sqlite_insert(self._table)
        ).values(rows)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["project_scope_key", "work_id", "run_id", "sequence"]
                )
            )
        return result.rowcount

    async def read(
        self, work_id: str, *, run_id: str, project_id: str | None, after: int = 0, limit: int = 500
    ) -> list[OperatorActivity]:
        table = self._table
        query = (
            select(table.c.event_json)
            .where(
                table.c.project_scope_key == project_scope_key(project_id),
                table.c.work_id == work_id,
                table.c.run_id == run_id,
                table.c.sequence > after,
            )
            .order_by(table.c.sequence)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [OperatorActivity.model_validate(row[0]) for row in rows]

    async def prune(self, *, project_id: str, completed_work_ids: Iterable[str], older_than: datetime) -> int:
        table = self._table
        ids = tuple(completed_work_ids)
        if not ids:
            return 0
        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(table).where(
                    table.c.project_scope_key == project_scope_key(project_id),
                    table.c.work_id.in_(ids),
                    table.c.created_at < older_than,
                )
            )
        return result.rowcount

    @staticmethod
    def _row(activity: OperatorActivity) -> dict:
        return {
            "project_scope_key": project_scope_key(activity.project_id),
            "work_id": activity.work_id,
            "run_id": activity.run_id,
            "sequence": activity.sequence,
            "event_json": activity.model_dump(mode="json"),
            "created_at": activity.at,
        }


__all__ = [
    "ACTIVITY_ROW_CAP",
    "ACTIVITY_LOG_MAX_BYTES",
    "ActivityKind",
    "ActivitySink",
    "ActivitySource",
    "ListActivitySink",
    "OperatorActivity",
    "WorkActivityStore",
    "activity_redactor",
]
