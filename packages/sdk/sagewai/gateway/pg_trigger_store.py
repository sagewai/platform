# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Postgres-backed TriggerStore using the ``connector_triggers`` table."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sagewai.gateway.triggers import EventFilter, Strategy, TriggerSpec, TriggerStore


class PostgresTriggerStore(TriggerStore):
    """Persists trigger configurations in the ``connector_triggers`` table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def save(self, trigger_id: str, trigger: TriggerSpec) -> None:
        filter_json = json.dumps(trigger.filter.model_dump())
        context_json = json.dumps(trigger.context)
        poll_seconds = (
            int(trigger.poll_interval.total_seconds())
            if trigger.poll_interval
            else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO connector_triggers
                       (id, source, strategy, poll_interval_seconds,
                        filter_json, target, action, context_json, enabled)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9)
                   ON CONFLICT (id)
                   DO UPDATE SET source = $2, strategy = $3,
                                 poll_interval_seconds = $4,
                                 filter_json = $5::jsonb, target = $6,
                                 action = $7, context_json = $8::jsonb,
                                 enabled = $9, updated_at = now()""",
                trigger_id,
                trigger.source,
                trigger.strategy.value,
                poll_seconds,
                filter_json,
                trigger.target,
                trigger.action,
                context_json,
                trigger.enabled,
            )

    async def get(self, trigger_id: str) -> TriggerSpec | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM connector_triggers WHERE id = $1", trigger_id,
            )
            return self._row_to_trigger(row) if row else None

    async def list_all(self) -> list[tuple[str, TriggerSpec]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM connector_triggers ORDER BY created_at",
            )
            return [(r["id"], self._row_to_trigger(r)) for r in rows]

    async def delete(self, trigger_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM connector_triggers WHERE id = $1", trigger_id,
            )

    @staticmethod
    def _row_to_trigger(row: Any) -> TriggerSpec:
        filter_data = row["filter_json"]
        if isinstance(filter_data, str):
            filter_data = json.loads(filter_data)
        context_data = row["context_json"]
        if isinstance(context_data, str):
            context_data = json.loads(context_data)
        poll_seconds = row["poll_interval_seconds"]
        return TriggerSpec(
            source=row["source"],
            strategy=Strategy(row["strategy"]),
            poll_interval=timedelta(seconds=poll_seconds) if poll_seconds else None,
            filter=EventFilter(**filter_data),
            target=row["target"],
            action=row["action"],
            context=context_data or {},
            enabled=row["enabled"],
        )
