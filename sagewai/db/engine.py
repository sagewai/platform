# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Async SQLAlchemy engine factory.

Used by Alembic's env.py for schema diffing. Stores can optionally
use this for connection pooling in the future.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_engine(database_url: str, **kwargs) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: PostgreSQL connection string.
            Must use the ``postgresql+asyncpg://`` scheme.
    """
    # Normalize the URL scheme for SQLAlchemy
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    defaults = {
        "pool_size": 5,
        "max_overflow": 10,
        "echo": False,
    }
    defaults.update(kwargs)
    return create_async_engine(database_url, **defaults)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory from an engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
