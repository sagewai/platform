# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Postgres-backed store for persisting playground agent specs.

Rewritten onto SQLAlchemy Core — works on both SQLite (default) and
PostgreSQL. The class name and all public method signatures are unchanged
so callers require no modification.

Usage::

    from sagewai.admin.agent_store import PostgresAgentStore

    # Default engine (SQLite or $SAGEWAI_DATABASE_URL):
    store = PostgresAgentStore()
    await store.init()

    # Explicit URL (old positional / keyword form — still supported):
    store = PostgresAgentStore("postgresql://user:pass@host/db")

    # Injected engine (test / DI form):
    store = PostgresAgentStore(engine=my_async_engine)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, PlaygroundAgentModel

logger = logging.getLogger(__name__)

_tbl = PlaygroundAgentModel.__table__

# Fields that carry transient LLM-provider state and must not be persisted.
_TRANSIENT_FIELDS: frozenset[str] = frozenset(("api_base", "api_key", "custom_llm_provider"))


class PostgresAgentStore:
    """Persists playground AgentSpec records to the playground_agents table.

    Uses SQLAlchemy Core for all operations — compatible with both SQLite
    (default, used in development / single-node deployments) and PostgreSQL
    (production).

    Constructor forms (all equivalent from caller perspective):

    * ``PostgresAgentStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``PostgresAgentStore("postgresql://user:pass@host/db")``
        Positional URL string — back-compat with old callers.
    * ``PostgresAgentStore(engine=my_engine)``
        Injected engine; used by tests and DI containers.
    * ``PostgresAgentStore(database_url="...")``
        Keyword URL — also supported.
    * ``PostgresAgentStore(pool=<asyncpg pool>)``
        *pool* is accepted but **ignored** — back-compat with callers that
        formerly passed an asyncpg pool directly.

    On SQLite, :meth:`init` creates the schema via ``create_all``.
    On PostgreSQL, :meth:`init` is a no-op (Alembic owns the schema).
    """

    def __init__(
        self,
        engine_or_url: AsyncEngine | str | None = None,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
        pool: Any = None,  # kept for API back-compat; not used
    ) -> None:
        # Resolve which engine to use, in priority order:
        #   1. engine= keyword argument
        #   2. positional AsyncEngine
        #   3. positional str URL
        #   4. database_url= keyword URL
        #   5. factory default
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif isinstance(engine_or_url, AsyncEngine):
            self._engine = engine_or_url
        elif isinstance(engine_or_url, str):
            self._engine = create_engine(engine_or_url)
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()
        # pool is intentionally ignored; SQLAlchemy engine owns connection pooling

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def save(self, name: str, spec_dict: dict[str, Any]) -> None:
        """Insert or update an agent spec.

        Transient provider fields (``api_base``, ``api_key``,
        ``custom_llm_provider``) are stripped before persisting.
        """
        persistable = {k: v for k, v in spec_dict.items() if k not in _TRANSIENT_FIELDS}
        now = datetime.now(timezone.utc)
        spec_json = json.dumps(persistable)

        stmt = upsert(
            _tbl,
            {
                "name": name,
                "spec": spec_json,
                "updated_at": now,
            },
            index_elements=["name"],
            set_={
                "spec": spec_json,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete(self, name: str) -> bool:
        """Delete an agent spec. Returns True if a row was deleted."""
        stmt = sa_delete(_tbl).where(_tbl.c.name == name)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    async def rename(self, old_name: str, new_name: str) -> bool:
        """Rename an agent. Returns True on success.

        Loads the existing spec, updates the ``name`` field inside the JSON
        blob, deletes the old row, and inserts a new row — wrapped in a
        transaction so the operation is atomic.
        """
        # Fetch existing row
        stmt = select(_tbl.c.spec).where(_tbl.c.name == old_name)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if row is None:
            return False

        try:
            spec_dict = json.loads(row["spec"])
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt agent spec for %r — cannot rename", old_name)
            return False

        spec_dict["name"] = new_name
        now = datetime.now(timezone.utc)
        new_spec_json = json.dumps(spec_dict)

        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(_tbl).where(_tbl.c.name == old_name))
            await conn.execute(
                _tbl.insert().values(
                    name=new_name,
                    spec=new_spec_json,
                    updated_at=now,
                )
            )
        return True

    async def list_all(self) -> list[dict[str, Any]]:
        """Load all persisted agent specs, ordered by creation time."""
        stmt = select(_tbl).order_by(_tbl.c.created_at)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()

        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                results.append(json.loads(row["spec"]))
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt agent spec for %r — skipping", row["name"])
        return results
