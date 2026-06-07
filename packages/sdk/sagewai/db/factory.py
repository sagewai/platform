# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Process-wide engine selection and SQLite schema bootstrap.

Environment variables (in precedence order):
  SAGEWAI_DATABASE_URL  -> explicit DB URL (takes highest precedence).
  DATABASE_URL          -> compatibility alias honoured by docker-compose,
                          apps/backend entrypoint, and Heroku-style hosts.
No DATABASE_URL / SAGEWAI_DATABASE_URL -> SQLite at db_dir()/sagewai.db (WAL).

postgresql://...  -> Postgres (asyncpg); schema via Alembic.
sqlite:///path    -> that SQLite file.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai import home
from sagewai.db.engine import create_engine
from sagewai.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def resolve_database_url() -> str:
    """Resolve the DB URL. Precedence: SAGEWAI_DATABASE_URL > DATABASE_URL (compat alias)
    > SQLite at db_dir()/sagewai.db (the zero-config default)."""
    override = os.environ.get("SAGEWAI_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if override:
        return override  # create_engine normalizes the scheme
    return f"sqlite+aiosqlite:///{home.db_dir() / 'sagewai.db'}"


def get_engine() -> AsyncEngine:
    """Return the cached process-wide async engine (created on first call)."""
    global _engine
    if _engine is None:
        _engine = create_engine(resolve_database_url())
        logger.info("Database engine initialized (dialect=%s)", _engine.dialect.name)
    return _engine


def is_sqlite() -> bool:
    return get_engine().dialect.name == "sqlite"


async def ensure_schema() -> None:
    """Bootstrap the SQLite schema via create_all. No-op on Postgres (Alembic owns it)."""
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose and clear the cached engine (async — releases pooled connections)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_workflow_store():
    """Return an INITIALIZED workflow store for the active backend.

    SQLite (default) → SqliteWorkflowStore (pool created via create_all).
    Postgres (DATABASE_URL / SAGEWAI_DATABASE_URL) → PostgresStore (asyncpg pool
    created and ready).

    Always awaits ``store.initialize()`` before returning so the caller
    can use the store immediately without a separate init step.
    """
    if is_sqlite():
        from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
        store = SqliteWorkflowStore(get_engine())
    else:
        from sagewai.core.stores.postgres import PostgresStore
        # resolve_database_url() honours DATABASE_URL alias (FIX 1).
        # asyncpg.create_pool wants a plain postgresql:// scheme, not
        # postgresql+asyncpg://.  SAGEWAI_DATABASE_URL / DATABASE_URL are
        # conventionally set as postgresql://, so resolve_database_url()
        # returns them verbatim and asyncpg is satisfied.
        store = PostgresStore(database_url=resolve_database_url())
    await store.initialize()
    return store


def reset_engine() -> None:
    """Synchronously clear the cached engine reference (tests). Does not dispose async resources."""
    global _engine
    _engine = None
