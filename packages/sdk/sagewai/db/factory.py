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


def add_missing_sqlite_columns(sync_conn) -> None:
    """Upgrade an existing SQLite home in place by adding model columns it lacks.

    ``create_all`` builds *new* tables but never ALTERs *existing* ones, so a home
    created before a column-adding migration is missing those columns — and a
    fail-closed startup probe (e.g. the fleet_tasks lease columns from migration 020)
    then refuses to boot with a raw SQL error. Add every model column the live table
    is missing so an older local home upgrades transparently on the next start.

    SQLite-only (Postgres upgrades are owned by Alembic). Strictly additive — never
    drops or retypes. Each column/index is attempted in its own SAVEPOINT so one
    failure can't poison the others, and a column that can't be added with its full
    spec is retried as a plain nullable column so it at least *exists* — a missing
    column crashes a non-probed table at runtime, which is worse than a missing default.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    def _try(clause: str) -> bool:
        sp = sync_conn.begin_nested()  # SAVEPOINT: isolate this statement's failure
        try:
            sync_conn.execute(sa_text(clause))
            sp.commit()
            return True
        except Exception:  # noqa: BLE001
            sp.rollback()
            return False

    insp = sa_inspect(sync_conn)
    ddl = sync_conn.dialect.ddl_compiler(sync_conn.dialect, None)
    existing_tables = set(insp.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all already built it at the current schema
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            # Render the column the SAME way create_all does — correct quoting for
            # string/empty defaults, CURRENT_TIMESTAMP for func.now(), etc. — rather
            # than hand-building DEFAULT SQL from the raw server_default text.
            candidates: list[str] = []
            try:
                candidates.append(
                    f'ALTER TABLE "{table.name}" ADD COLUMN {ddl.get_column_specification(col)}'
                )
            except Exception:  # noqa: BLE001 — fall back to a bare column below
                pass
            coltype = col.type.compile(dialect=sync_conn.dialect)
            candidates.append(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}')
            if any(_try(c) for c in candidates):
                logger.info("sqlite home upgrade: added %s.%s", table.name, col.name)
            else:
                logger.warning("sqlite home upgrade: could not add %s.%s", table.name, col.name)
        # create_all won't add an index to a table that already exists (e.g. an index
        # over a column we just added), so create any the live table is missing.
        for index in table.indexes:
            sp = sync_conn.begin_nested()
            try:
                index.create(bind=sync_conn, checkfirst=True)
                sp.commit()
            except Exception:  # noqa: BLE001 — best-effort; probe is the backstop
                sp.rollback()
                logger.warning("sqlite home upgrade: could not add index %s", index.name)


async def ensure_schema() -> None:
    """Bootstrap + upgrade the SQLite schema. No-op on Postgres (Alembic owns it).

    Creates any missing tables, then ALTERs in any model columns (and adds any indexes)
    an *existing* table lacks, so a home from an older version upgrades in place
    (e.g. the fleet_tasks lease columns + ix_fleet_tasks_lease from migration 020).
    """
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(add_missing_sqlite_columns)


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
