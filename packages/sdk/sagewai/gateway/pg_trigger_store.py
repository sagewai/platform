# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Postgres-backed TriggerStore using SQLAlchemy Core.

Replaces the former asyncpg-only implementation of PostgresTriggerStore.
Works on both SQLite (default) and PostgreSQL via a shared AsyncEngine.

.. warning::

    **INTERNAL / single-org or legacy only — NOT tenant-safe.** Do not use for
    multi-tenant admin resources (superseded by ``AdminResourceStore``, #444).
    This store keys trigger rows by ``id`` ALONE — ``project_id`` is not enforced,
    so any caller with a trigger id can read/overwrite/delete a trigger belonging
    to another project or org. It remains only for the single-org gateway and its
    existing tests. New multi-tenant code MUST route through a project-scoped
    store. Instantiating it emits a :class:`DeprecationWarning`.
"""

from __future__ import annotations

import warnings
from datetime import timedelta
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, ConnectorTriggerModel
from sagewai.gateway.triggers import EventFilter, Strategy, TriggerSpec, TriggerStore


def _make_engine(
    database_url: str | None,
    pool: Any,
    engine: AsyncEngine | None,
) -> AsyncEngine:
    """Resolve the engine from the three possible constructor inputs."""
    if engine is not None:
        return engine
    if database_url is not None:
        return create_engine(database_url)
    # pool is ignored (asyncpg back-compat)
    return factory.get_engine()


class PostgresTriggerStore(TriggerStore):
    """Persists trigger configurations in the ``connector_triggers`` table.

    .. warning::

        **INTERNAL / single-org or legacy only — NOT tenant-safe; do not use for
        multi-tenant admin resources (superseded by AdminResourceStore).
        project_id is not enforced** (rows are keyed by ``id`` alone). Use a
        project-scoped store for any multi-tenant path. Constructing this class
        emits a :class:`DeprecationWarning`.

    Parameters
    ----------
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* and
        *pool* are ignored.
    database_url:
        Connection string passed to :func:`sagewai.db.engine.create_engine`.
        Ignored when *engine* is supplied.
    pool:
        Accepted for backwards-compatibility with callers that previously
        passed an asyncpg pool.  It is **not used** by this implementation;
        the SQLAlchemy engine manages its own connection pool.
    """

    def __init__(
        self,
        pool: Any = None,  # kept for API back-compat; not used
        database_url: str | None = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        warnings.warn(
            "PostgresTriggerStore is INTERNAL / single-org or legacy only and is "
            "NOT tenant-safe (project_id is not enforced; rows are keyed by id "
            "alone). It is superseded by AdminResourceStore for multi-tenant admin "
            "resources — do not use it in new multi-tenant code.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._engine: AsyncEngine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def save(self, trigger_id: str, trigger: TriggerSpec) -> None:
        """Upsert a trigger — ON CONFLICT (id) DO UPDATE."""
        tbl = ConnectorTriggerModel.__table__
        poll_seconds = (
            int(trigger.poll_interval.total_seconds())
            if trigger.poll_interval is not None
            else None
        )
        values = {
            "id": trigger_id,
            "source": trigger.source,
            "strategy": trigger.strategy.value,
            "poll_interval_seconds": poll_seconds,
            "filter_json": trigger.filter.model_dump(),
            "target": trigger.target,
            "action": trigger.action,
            "context_json": trigger.context,
            "enabled": trigger.enabled,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["id"],
            set_={
                "source": trigger.source,
                "strategy": trigger.strategy.value,
                "poll_interval_seconds": poll_seconds,
                "filter_json": trigger.filter.model_dump(),
                "target": trigger.target,
                "action": trigger.action,
                "context_json": trigger.context,
                "enabled": trigger.enabled,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, trigger_id: str) -> TriggerSpec | None:
        """Get a trigger by ID."""
        tbl = ConnectorTriggerModel.__table__
        stmt = select(tbl).where(tbl.c.id == trigger_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return self._row_to_trigger(row) if row is not None else None

    async def list_all(self) -> list[tuple[str, TriggerSpec]]:
        """List all triggers, ordered by created_at."""
        tbl = ConnectorTriggerModel.__table__
        stmt = select(tbl).order_by(tbl.c.created_at)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [(row["id"], self._row_to_trigger(row)) for row in rows]

    async def delete(self, trigger_id: str) -> None:
        """Delete a trigger by ID."""
        stmt = sa_delete(ConnectorTriggerModel.__table__).where(
            ConnectorTriggerModel.__table__.c.id == trigger_id
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    @staticmethod
    def _row_to_trigger(row: Any) -> TriggerSpec:
        """Convert a SQLAlchemy row mapping to a TriggerSpec."""
        import json

        filter_data = row["filter_json"]
        if isinstance(filter_data, str):
            filter_data = json.loads(filter_data)
        if filter_data is None:
            filter_data = {}

        context_data = row["context_json"]
        if isinstance(context_data, str):
            context_data = json.loads(context_data)
        if context_data is None:
            context_data = {}

        poll_seconds = row["poll_interval_seconds"]
        return TriggerSpec(
            source=row["source"],
            strategy=Strategy(row["strategy"]),
            poll_interval=timedelta(seconds=poll_seconds) if poll_seconds is not None else None,
            filter=EventFilter(**filter_data),
            target=row["target"],
            action=row["action"],
            context=context_data,
            enabled=row["enabled"],
        )
