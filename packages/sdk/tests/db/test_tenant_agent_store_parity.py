# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresTenantAgentStore — runs against both SQLite and Postgres.

Covers the tenant-scoped agent config store:
- project isolation + org-shared (global) inheritance (shadow-resolution)
- a project may not delete an org-shared (global) row; the org may
- rename within write scope

No encryption/secrets: the fixture is simpler — just store + dialect_engine.
"""

from __future__ import annotations

import pytest

from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_agent_store import PostgresTenantAgentStore


def _ctx(project_id):
    return RequestContext(
        actor=UserRef("u", "u"),
        org_id="default",
        project_id=project_id,
        roles=frozenset(),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode="multi",
    )


@pytest.fixture
async def store(dialect_engine):
    s = PostgresTenantAgentStore(engine=dialect_engine)
    await s.init()
    return s


@pytest.mark.asyncio
async def test_same_name_across_projects_isolated(store):
    await store.create({"name": "scout", "model": "x"}, ctx=_ctx("P"))
    await store.create({"name": "scout", "model": "y"}, ctx=_ctx("Q"))
    p = await store.get("scout", ctx=_ctx("P"))
    q = await store.get("scout", ctx=_ctx("Q"))
    assert p["model"] == "x" and q["model"] == "y"


@pytest.mark.asyncio
async def test_global_inheritance_and_shadowing(store):
    await store.create({"name": "shared", "model": "g"}, ctx=_ctx(None))
    await store.create({"name": "local", "model": "l"}, ctx=_ctx("P"))
    names_p = sorted(a["name"] for a in await store.list(ctx=_ctx("P")))
    assert names_p == ["local", "shared"]
    names_q = sorted(a["name"] for a in await store.list(ctx=_ctx("Q")))
    assert names_q == ["shared"]
    # project-local shadows a global of the same name
    await store.create({"name": "shared", "model": "override"}, ctx=_ctx("P"))
    assert (await store.get("shared", ctx=_ctx("P")))["model"] == "override"
    assert (await store.get("shared", ctx=_ctx("Q")))["model"] == "g"


@pytest.mark.asyncio
async def test_project_cannot_delete_global(store):
    await store.create({"name": "shared", "model": "g"}, ctx=_ctx(None))
    assert await store.delete("shared", ctx=_ctx("P")) is False
    assert await store.delete("shared", ctx=_ctx(None)) is True


@pytest.mark.asyncio
async def test_rename_scoped(store):
    await store.create({"name": "old", "model": "x"}, ctx=_ctx("P"))
    out = await store.rename("old", "new", ctx=_ctx("P"))
    assert out["name"] == "new"
    assert await store.get("old", ctx=_ctx("P")) is None
    assert (await store.get("new", ctx=_ctx("P")))["model"] == "x"
