# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""FleetTaskModel gains lease_expires_at + attempts + the lease index."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from sagewai.db.engine import create_engine
from sagewai.db.models import Base


@pytest.mark.asyncio
async def test_lease_columns_and_index(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _reflect(c):
            insp = inspect(c)
            return ({col["name"]: col for col in insp.get_columns("fleet_tasks")},
                    {ix["name"] for ix in insp.get_indexes("fleet_tasks")})

        cols, indexes = await conn.run_sync(_reflect)
    assert {"lease_expires_at", "attempts"} <= set(cols)
    assert cols["lease_expires_at"]["nullable"] is True
    assert cols["attempts"]["nullable"] is False
    assert cols["attempts"]["default"] is not None     # server_default '0'
    assert "ix_fleet_tasks_lease" in indexes
