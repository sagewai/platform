# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for AdminResourceStore — runs against both SQLite and Postgres.

Covers the generic project-scoped admin control-plane resource store that backs
all the currently-file-backed admin resources (budgets, guardrails, saved
workflows, eval datasets, notification channels/triggers, connector triggers,
artifact destinations) in multi-tenant mode:

- project isolation: PA's rows hidden from PB (cross-project hidden)
- org-shared (project_id=None) rows readable by every project, mutable by none
- write scope: PB cannot update/delete PA's row
- name uniqueness per (kind, scope) via the partial-unique index
- different ``kind``s with the same resource_id/name don't collide

No encryption/secrets: the store keeps ``data`` opaque — secret handling is the
route's job (a later step). Fixture is just store + dialect_engine.
"""

from __future__ import annotations

import pytest

from sagewai.admin.admin_resource_store import (
    AdminResourceStore,
    ResourceConflictError,
    ResourceWriteScopeError,
)
from sagewai.admin.tenancy import RequestContext, UserRef

KIND = "budget"


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
    s = AdminResourceStore(engine=dialect_engine)
    await s.init()
    return s


# --------------------------------------------------------------- (a) isolation


@pytest.mark.asyncio
async def test_project_isolation_cross_project_hidden(store):
    await store.upsert_for(_ctx("P"), KIND, "b1", {"max": 10}, name="daily")
    # PA sees its own row via list and get
    assert [r["max"] for r in await store.list_for(_ctx("P"), KIND)] == [10]
    assert (await store.get_for(_ctx("P"), KIND, "b1"))["max"] == 10
    # PB sees nothing — cross-project hidden
    assert await store.list_for(_ctx("Q"), KIND) == []
    assert await store.get_for(_ctx("Q"), KIND, "b1") is None


# ------------------------------------------------------- (b) org-shared inherit


@pytest.mark.asyncio
async def test_org_shared_visible_to_all_not_writable_by_project(store):
    await store.upsert_for(_ctx(None), KIND, "shared", {"max": 99}, name="org-wide")
    # visible to both projects (read inheritance)
    assert (await store.get_for(_ctx("P"), KIND, "shared"))["max"] == 99
    assert (await store.get_for(_ctx("Q"), KIND, "shared"))["max"] == 99
    assert [r["max"] for r in await store.list_for(_ctx("P"), KIND)] == [99]
    # a project ctx may not mutate the org-shared row (update path)
    with pytest.raises(ResourceWriteScopeError):
        await store.upsert_for(_ctx("P"), KIND, "shared", {"max": 1}, name="org-wide")
    # nor delete it
    assert await store.delete_for(_ctx("P"), KIND, "shared") is False
    # org ctx can update its own shared row
    out = await store.upsert_for(_ctx(None), KIND, "shared", {"max": 50}, name="org-wide")
    assert out["max"] == 50


# ----------------------------------------------------------- (c) write scope


@pytest.mark.asyncio
async def test_project_cannot_mutate_other_projects_row(store):
    await store.upsert_for(_ctx("P"), KIND, "b1", {"max": 10}, name="daily")
    # PB cannot delete PA's row
    assert await store.delete_for(_ctx("Q"), KIND, "b1") is False
    # PB cannot update PA's row (same resource_id, different project)
    with pytest.raises(ResourceWriteScopeError):
        await store.upsert_for(_ctx("Q"), KIND, "b1", {"max": 999}, name="daily")
    # PA's row untouched
    assert (await store.get_for(_ctx("P"), KIND, "b1"))["max"] == 10
    # PA can delete its own row
    assert await store.delete_for(_ctx("P"), KIND, "b1") is True
    assert await store.get_for(_ctx("P"), KIND, "b1") is None


# ------------------------------------------------------------- (d) name unique


@pytest.mark.asyncio
async def test_name_unique_within_same_project_scope(store):
    await store.upsert_for(_ctx("P"), KIND, "b1", {"v": 1}, name="daily")
    with pytest.raises(ResourceConflictError):
        await store.upsert_for(_ctx("P"), KIND, "b2", {"v": 2}, name="daily")


@pytest.mark.asyncio
async def test_same_name_different_projects_allowed(store):
    a = await store.upsert_for(_ctx("P"), KIND, "b1", {"v": 1}, name="daily")
    b = await store.upsert_for(_ctx("Q"), KIND, "b2", {"v": 2}, name="daily")
    assert a["v"] == 1 and b["v"] == 2
    # name-based lookup is scoped
    assert (await store.get_by_name_for(_ctx("P"), KIND, "daily"))["v"] == 1
    assert (await store.get_by_name_for(_ctx("Q"), KIND, "daily"))["v"] == 2


@pytest.mark.asyncio
async def test_one_global_plus_one_per_project_name_allowed(store):
    await store.upsert_for(_ctx(None), KIND, "g", {"v": 0}, name="daily")
    await store.upsert_for(_ctx("P"), KIND, "p", {"v": 1}, name="daily")
    # both distinct rows exist; org-shared visible to P alongside P's own
    vals = sorted(r["v"] for r in await store.list_for(_ctx("P"), KIND))
    assert vals == [0, 1]


@pytest.mark.asyncio
async def test_resaving_same_resource_id_with_same_name_is_update_not_conflict(store):
    await store.upsert_for(_ctx("P"), KIND, "b1", {"v": 1}, name="daily")
    out = await store.upsert_for(_ctx("P"), KIND, "b1", {"v": 2}, name="daily")
    assert out["v"] == 2
    assert (await store.get_for(_ctx("P"), KIND, "b1"))["v"] == 2


# -------------------------------------------------------- (e) kind separation


@pytest.mark.asyncio
async def test_different_kinds_same_id_and_name_dont_collide(store):
    await store.upsert_for(_ctx("P"), "budget", "shared-id", {"v": 1}, name="dup")
    await store.upsert_for(_ctx("P"), "guardrail", "shared-id", {"v": 2}, name="dup")
    assert (await store.get_for(_ctx("P"), "budget", "shared-id"))["v"] == 1
    assert (await store.get_for(_ctx("P"), "guardrail", "shared-id"))["v"] == 2
    # list_for is kind-scoped
    assert [r["v"] for r in await store.list_for(_ctx("P"), "budget")] == [1]
    assert [r["v"] for r in await store.list_for(_ctx("P"), "guardrail")] == [2]


@pytest.mark.asyncio
async def test_get_by_name_respects_scope(store):
    await store.upsert_for(_ctx("P"), KIND, "b1", {"v": 1}, name="daily")
    # PB cannot resolve PA's name
    assert await store.get_by_name_for(_ctx("Q"), KIND, "daily") is None
