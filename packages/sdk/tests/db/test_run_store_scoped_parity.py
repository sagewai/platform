# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for the ctx-scoped RunStore methods (SQLite + Postgres).

Covers the §3 data-scope contract on the run-telemetry surface:
- save_run_for stamps ctx.project_id (never the body)
- list_runs_for returns own + org-shared (global), never another project's
- get_run_for hides cross-project rows (None)
- delete_run_for is write-scoped: a project cannot delete a global/other row,
  the owner can.

Uses the ``dialect_engine`` fixture (SQLite always; Postgres when
SAGEWAI_TEST_DATABASE_URL is set).
"""

from __future__ import annotations

import pytest

from sagewai.admin.store import RunStore
from sagewai.admin.tenancy import RequestContext, UserRef


def _ctx(project_id):
    return RequestContext(
        actor=UserRef("u", "u"),
        org_id="default",
        project_id=project_id,
        roles=frozenset({"org:admin"} if project_id is None else {"project:admin"}),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode="multi",
    )


@pytest.fixture
async def store(dialect_engine):
    s = RunStore(engine=dialect_engine)
    await s.init()
    return s


@pytest.mark.asyncio
async def test_save_run_for_stamps_ctx_project(store):
    rid = await store.save_run_for(_ctx("P"), agent_name="scout")
    rec = await store.get_run_for(rid, _ctx("P"))
    assert rec is not None
    assert rec.project_id == "P"
    # a global save (ctx.project_id=None) stamps NULL and is visible to all
    gid = await store.save_run_for(_ctx(None), agent_name="shared")
    grec = await store.get_run_for(gid, _ctx(None))
    assert grec is not None
    assert grec.project_id is None
    assert (await store.get_run_for(gid, _ctx("P"))) is not None


@pytest.mark.asyncio
async def test_save_run_for_honours_supplied_run_id(store):
    # When run_id is given it is persisted verbatim (so a route can emit an id
    # before the run finishes and still resolve it afterwards).
    rid = await store.save_run_for(_ctx("P"), run_id="run-fixed123", agent_name="scout")
    assert rid == "run-fixed123"
    rec = await store.get_run_for("run-fixed123", _ctx("P"))
    assert rec is not None
    assert rec.run_id == "run-fixed123"
    # the generate path still works when run_id is omitted
    gen = await store.save_run_for(_ctx("P"), agent_name="scout")
    assert gen != "run-fixed123"
    assert (await store.get_run_for(gen, _ctx("P"))) is not None


@pytest.mark.asyncio
async def test_list_runs_for_own_plus_global_never_other(store):
    p = await store.save_run_for(_ctx("P"), agent_name="p_run")
    q = await store.save_run_for(_ctx("Q"), agent_name="q_run")
    g = await store.save_run_for(_ctx(None), agent_name="g_run")

    p_ids = {r.run_id for r in await store.list_runs_for(_ctx("P"))}
    assert p in p_ids
    assert g in p_ids  # inherits global
    assert q not in p_ids  # never another project's


@pytest.mark.asyncio
async def test_get_run_for_hides_cross_project(store):
    q = await store.save_run_for(_ctx("Q"), agent_name="q_run")
    assert (await store.get_run_for(q, _ctx("P"))) is None
    assert (await store.get_run_for(q, _ctx("Q"))) is not None


@pytest.mark.asyncio
async def test_delete_run_for_write_scope(store):
    g = await store.save_run_for(_ctx(None), agent_name="g_run")
    # a project cannot delete a global (inherited) row
    assert (await store.delete_run_for(g, _ctx("P"))) is False
    q = await store.save_run_for(_ctx("Q"), agent_name="q_run")
    # ...nor another project's row
    assert (await store.delete_run_for(q, _ctx("P"))) is False
    # the org owner can delete the global
    assert (await store.delete_run_for(g, _ctx(None))) is True
    # the owner can delete its own
    p = await store.save_run_for(_ctx("P"), agent_name="p_run")
    assert (await store.delete_run_for(p, _ctx("P"))) is True
