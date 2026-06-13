# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.db.engine — Postgres and SQLite engine creation."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from sagewai.db.engine import create_engine


@pytest.mark.asyncio
async def test_sqlite_engine_enables_wal(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_engine(url)
    async with engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    await engine.dispose()
    assert str(mode).lower() == "wal"


@pytest.mark.asyncio
async def test_sqlite_bare_scheme_is_normalized(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")  # bare sqlite:// → aiosqlite
    assert engine.dialect.name == "sqlite"
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_foreign_keys_on(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.connect() as conn:
        fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
    await engine.dispose()
    assert int(fk) == 1


@pytest.mark.asyncio
async def test_sqlite_busy_timeout_set(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.connect() as conn:
        timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    await engine.dispose()
    assert int(timeout) == 5000


def test_postgres_url_still_normalized():
    engine = create_engine("postgresql://u:p@localhost/db")
    assert "+asyncpg" in str(engine.url)


def test_postgres_short_scheme_normalized():
    # `postgres://` (the common shorthand / docker-compose default) must map to
    # the asyncpg driver; SQLAlchemy rejects the bare `postgres` dialect.
    engine = create_engine("postgres://u:p@localhost/db")
    assert engine.url.drivername == "postgresql+asyncpg"
