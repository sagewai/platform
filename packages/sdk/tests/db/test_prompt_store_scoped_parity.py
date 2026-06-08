# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for the ctx-scoped PromptStore methods (SQLite + Postgres).

Covers the §3 data-scope contract on the prompt-log surface:
- save_prompt_log_for stamps ctx.project_id (never the body)
- list_prompt_logs_for / list_examples_for return own + org-shared, never
  another project's
- get_prompt_log_for hides cross-project rows (None)
- update_prompt_log_for / delete_prompt_log_for are write-scoped: a project
  cannot mutate/delete a global or another project's row; the owner can.

Uses the ``dialect_engine`` fixture (SQLite always; Postgres when
SAGEWAI_TEST_DATABASE_URL is set).
"""

from __future__ import annotations

import pytest

from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.observability.prompt_store import PromptStore


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
    s = PromptStore(engine=dialect_engine)
    await s.init()
    return s


@pytest.mark.asyncio
async def test_save_prompt_log_for_stamps_ctx_project(store):
    lid = await store.save_prompt_log_for(_ctx("P"), agent_name="scout")
    rec = await store.get_prompt_log_for(lid, _ctx("P"))
    assert rec is not None
    assert rec.project_id == "P"
    # a global save stamps NULL and is visible to all
    gid = await store.save_prompt_log_for(_ctx(None), agent_name="shared")
    grec = await store.get_prompt_log_for(gid, _ctx(None))
    assert grec is not None
    assert grec.project_id is None
    assert (await store.get_prompt_log_for(gid, _ctx("P"))) is not None


@pytest.mark.asyncio
async def test_list_prompt_logs_for_own_plus_global_never_other(store):
    p = await store.save_prompt_log_for(_ctx("P"), agent_name="p_log")
    q = await store.save_prompt_log_for(_ctx("Q"), agent_name="q_log")
    g = await store.save_prompt_log_for(_ctx(None), agent_name="g_log")

    p_ids = {r.log_id for r in await store.list_prompt_logs_for(_ctx("P"))}
    assert p in p_ids
    assert g in p_ids  # inherits global
    assert q not in p_ids  # never another project's


@pytest.mark.asyncio
async def test_get_prompt_log_for_hides_cross_project(store):
    q = await store.save_prompt_log_for(_ctx("Q"), agent_name="q_log")
    assert (await store.get_prompt_log_for(q, _ctx("P"))) is None
    assert (await store.get_prompt_log_for(q, _ctx("Q"))) is not None


@pytest.mark.asyncio
async def test_list_examples_for_own_plus_global_never_other(store):
    await store.save_prompt_log_for(_ctx("P"), agent_name="scout", is_example=True)
    await store.save_prompt_log_for(_ctx(None), agent_name="scout", is_example=True)
    await store.save_prompt_log_for(_ctx("Q"), agent_name="scout", is_example=True)
    # non-example excluded
    await store.save_prompt_log_for(_ctx("P"), agent_name="scout", is_example=False)

    examples = await store.list_examples_for(_ctx("P"), agent_name="scout")
    pids = sorted({r.project_id for r in examples}, key=lambda x: (x is None, x))
    assert pids == ["P", None]  # own + global, never Q's
    assert all(r.is_example for r in examples)


@pytest.mark.asyncio
async def test_update_prompt_log_for_write_scope(store):
    g = await store.save_prompt_log_for(_ctx(None), agent_name="g_log")
    # a project cannot mutate a global (inherited) row
    assert (await store.update_prompt_log_for(g, _ctx("P"), is_example=True)) is None
    q = await store.save_prompt_log_for(_ctx("Q"), agent_name="q_log")
    assert (await store.update_prompt_log_for(q, _ctx("P"), is_example=True)) is None
    # the org owner can update the global
    updated = await store.update_prompt_log_for(g, _ctx(None), is_example=True, tags=["x"])
    assert updated is not None
    assert updated.is_example is True
    assert updated.tags == ["x"]
    # the owner can update its own
    p = await store.save_prompt_log_for(_ctx("P"), agent_name="p_log")
    assert (await store.update_prompt_log_for(p, _ctx("P"), is_example=True)) is not None


@pytest.mark.asyncio
async def test_update_prompt_log_for_quality_round_trips(store):
    # quality has no column; it is merged into metadata and surfaced by to_dict().
    p = await store.save_prompt_log_for(_ctx("P"), agent_name="scout")
    updated = await store.update_prompt_log_for(p, _ctx("P"), quality=5)
    assert updated is not None
    assert updated.to_dict()["quality"] == 5
    # a re-read confirms persistence and that metadata isn't clobbered
    rec = await store.get_prompt_log_for(p, _ctx("P"))
    assert rec.to_dict()["quality"] == 5
    # write-scoped: a project cannot rate a global/other row
    g = await store.save_prompt_log_for(_ctx(None), agent_name="g_log")
    assert (await store.update_prompt_log_for(g, _ctx("P"), quality=4)) is None


@pytest.mark.asyncio
async def test_delete_prompt_log_for_write_scope(store):
    g = await store.save_prompt_log_for(_ctx(None), agent_name="g_log")
    assert (await store.delete_prompt_log_for(g, _ctx("P"))) is False
    q = await store.save_prompt_log_for(_ctx("Q"), agent_name="q_log")
    assert (await store.delete_prompt_log_for(q, _ctx("P"))) is False
    assert (await store.delete_prompt_log_for(g, _ctx(None))) is True
    p = await store.save_prompt_log_for(_ctx("P"), agent_name="p_log")
    assert (await store.delete_prompt_log_for(p, _ctx("P"))) is True
