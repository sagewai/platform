# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""FleetTaskModel builds on SQLite via create_all with the pinned invariants."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from sagewai.db.engine import create_engine
from sagewai.db.models import Base


@pytest.mark.asyncio
async def test_fleet_tasks_table_builds(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _reflect(c):
            insp = inspect(c)
            return {
                "cols": {col["name"]: col for col in insp.get_columns("fleet_tasks")},
                "pk": insp.get_pk_constraint("fleet_tasks")["constrained_columns"],
                "indexes": {ix["name"] for ix in insp.get_indexes("fleet_tasks")},
                "checks": {ck.get("name") for ck in insp.get_check_constraints("fleet_tasks")},
            }

        meta = await conn.run_sync(_reflect)
    cols = meta["cols"]
    assert {"run_id", "org_id", "project_id", "pool", "model", "labels", "payload",
            "status", "worker_id", "claimed_at", "output", "error", "reported_at",
            "created_at"} <= set(cols)
    assert cols["org_id"]["nullable"] is False      # tenant isolation
    assert cols["project_id"]["nullable"] is True
    assert meta["pk"] == ["run_id"]                 # unique run identity
    assert "ck_fleet_tasks_status" in meta["checks"]    # status constrained
    assert {"ix_fleet_tasks_claim", "ix_fleet_tasks_scope"} <= meta["indexes"]
    # status defaults to 'pending' (server_default text varies by dialect; assert it exists)
    assert cols["status"]["default"] is not None
