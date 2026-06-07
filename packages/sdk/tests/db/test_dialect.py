# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.db.dialect — dialect-aware upsert helper and dual-dialect fixture."""

import pytest
from sqlalchemy import Column, MetaData, String, Table, select

from sagewai.db.engine import create_engine
from sagewai.db.dialect import upsert


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates_sqlite(tmp_path):
    md = MetaData()
    t = Table("kv", md, Column("k", String, primary_key=True), Column("v", String))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)
        await conn.execute(upsert(t, {"k": "a", "v": "1"}, index_elements=["k"], set_={"v": "1"}, dialect="sqlite"))
        await conn.execute(upsert(t, {"k": "a", "v": "2"}, index_elements=["k"], set_={"v": "2"}, dialect="sqlite"))
        rows = (await conn.execute(select(t.c.v))).scalars().all()
    await engine.dispose()
    assert rows == ["2"]


@pytest.mark.asyncio
async def test_upsert_default_set_uses_excluded(tmp_path):
    md = MetaData()
    t = Table("kv2", md, Column("k", String, primary_key=True), Column("v", String))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'u2.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)
        await conn.execute(upsert(t, {"k": "a", "v": "1"}, index_elements=["k"], dialect="sqlite"))
        await conn.execute(upsert(t, {"k": "a", "v": "9"}, index_elements=["k"], dialect="sqlite"))  # no set_ -> update all non-key cols from excluded
        v = (await conn.execute(select(t.c.v))).scalar_one()
    await engine.dispose()
    assert v == "9"


def test_upsert_builds_postgres_statement():
    md = MetaData()
    t = Table("kv3", md, Column("k", String, primary_key=True), Column("v", String))
    stmt = upsert(t, {"k": "a", "v": "1"}, index_elements=["k"], set_={"v": "1"}, dialect="postgresql")
    compiled = str(stmt.compile(dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()))
    assert "ON CONFLICT" in compiled


@pytest.mark.asyncio
async def test_dialect_engine_fixture_has_schema(dialect_engine):
    from sqlalchemy import inspect
    async with dialect_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert "workflow_runs" in tables
