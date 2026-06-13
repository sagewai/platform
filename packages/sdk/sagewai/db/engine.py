# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Async SQLAlchemy engine factory.

Supports both PostgreSQL (via asyncpg) and SQLite (via aiosqlite).
Used by Alembic's env.py for schema diffing. Stores can optionally
use this for connection pooling in the future.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_engine(database_url: str, **kwargs) -> AsyncEngine:
    """Create an async engine for Postgres (asyncpg) or SQLite (aiosqlite).

    - ``postgresql://`` (or the ``postgres://`` shorthand) → ``postgresql+asyncpg://``
      with a connection pool (``pool_size``/``max_overflow`` kwargs apply here).
    - ``sqlite://`` / ``sqlite+aiosqlite://`` → aiosqlite engine with WAL,
      foreign keys, and a busy timeout. Only ``echo`` is honored; pool
      kwargs are ignored (SQLite uses aiosqlite's default pool).
    """
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgres://"):
        # ``postgres://`` is a common shorthand (Heroku/Railway/many hosts and
        # tools, and the docker-compose default). SQLAlchemy dropped the bare
        # ``postgres`` dialect, so normalise it to the asyncpg driver just like
        # ``postgresql://`` — otherwise engine creation raises NoSuchModuleError.
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("sqlite://") and "+aiosqlite" not in database_url:
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    if database_url.startswith("sqlite+aiosqlite://"):
        engine = create_async_engine(database_url, echo=kwargs.get("echo", False))

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return engine

    defaults = {"pool_size": 5, "max_overflow": 10, "echo": False}
    defaults.update(kwargs)
    return create_async_engine(database_url, **defaults)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory from an engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
