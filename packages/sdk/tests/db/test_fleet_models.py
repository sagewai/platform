# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WorkerModel / EnrollmentKeyModel build on SQLite via create_all."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from sagewai.db.engine import create_engine
from sagewai.db.models import Base


@pytest.mark.asyncio
async def test_fleet_tables_build_on_sqlite(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert {"workers", "enrollment_keys"} <= names
    cols = None
    async with engine.begin() as conn:
        cols = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("workers")})
    assert {"worker_id", "project_id", "secret_hash", "name", "approved_at",
            "approved_by", "status", "capabilities"} <= cols
