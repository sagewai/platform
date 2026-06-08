# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Round-trip schema tests for the tenant provider table (migration 011)
and the tenant agent table (migration 012)."""

import pytest
from sqlalchemy import text

from sagewai.db.engine import create_engine
from sagewai.db.models import Base


@pytest.mark.asyncio
async def test_provider_partial_unique_allows_same_name_across_projects(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.execute(text("INSERT INTO provider (id, project_id, provider_name, is_default, data, created_at, updated_at)"
                             " VALUES ('a','P1','openai',0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        await c.execute(text("INSERT INTO provider (id, project_id, provider_name, is_default, data, created_at, updated_at)"
                             " VALUES ('b','P2','openai',0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    import sqlalchemy.exc as exc
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO provider (id, project_id, provider_name, is_default, data, created_at, updated_at)"
                             " VALUES ('g1',NULL,'openai',0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    with pytest.raises(exc.IntegrityError):
        async with engine.begin() as c:
            await c.execute(text("INSERT INTO provider (id, project_id, provider_name, is_default, data, created_at, updated_at)"
                                 " VALUES ('g2',NULL,'openai',0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_partial_unique_allows_same_name_across_projects(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        # Same name in different projects — allowed
        await c.execute(text(
            "INSERT INTO agent (id, project_id, name, spec, created_at, updated_at)"
            " VALUES ('a1','P1','scout','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        await c.execute(text(
            "INSERT INTO agent (id, project_id, name, spec, created_at, updated_at)"
            " VALUES ('a2','P2','scout','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
    import sqlalchemy.exc as exc
    # Global row inserts fine
    async with engine.begin() as c:
        await c.execute(text(
            "INSERT INTO agent (id, project_id, name, spec, created_at, updated_at)"
            " VALUES ('g1',NULL,'scout','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
    # Duplicate global name → IntegrityError
    with pytest.raises(exc.IntegrityError):
        async with engine.begin() as c:
            await c.execute(text(
                "INSERT INTO agent (id, project_id, name, spec, created_at, updated_at)"
                " VALUES ('g2',NULL,'scout','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ))
    await engine.dispose()
