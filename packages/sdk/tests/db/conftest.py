# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dual-dialect engine fixture for store parity tests.

Yields a schema-loaded AsyncEngine for each configured dialect:
- SQLite always (in-process, via aiosqlite).
- PostgreSQL only when SAGEWAI_TEST_DATABASE_URL is set.
"""
import os

import pytest_asyncio

from sagewai.db.engine import create_engine
from sagewai.db.models import Base

_PG_URL = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
_PARAMS = ["sqlite"] + (["postgres"] if _PG_URL else [])


@pytest_asyncio.fixture(params=_PARAMS)
async def dialect_engine(request, tmp_path):
    """Async engine with the full schema. SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set."""
    if request.param == "sqlite":
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'parity.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        engine = create_engine(_PG_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
