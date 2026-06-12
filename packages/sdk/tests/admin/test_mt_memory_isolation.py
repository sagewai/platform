# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cross-tenant isolation for the project memory + context surfaces (multi-tenant).

The 13 vector/graph/context admin routes are now PROJECT-SCOPED in multi-tenant
mode: every read/write derives its project from the session-validated
``RequestContext`` (never the ``X-Project-ID`` header), so project A can never see
or mutate project B's memory/context. Members manage their own project's memory;
a viewer is read-only.

``httpx.ASGITransport`` does not run the lifespan, so the per-project engine
resolver + the audit store are bound here the same way the lifespan does. The
resolver is wired with the durable backends (sqlite-vec vector, Postgres/SQLite
context store) so these tests exercise the real durable path, with each project
getting its own isolated namespace.

The discriminator throughout: project B's data is ingested THROUGH B's own
session (so it genuinely lives in B's scope); a forged ``X-Project-ID: pa`` from
a B session is a no-op (the middleware ignores the header for tenancy), and a PA
session never sees B's rows.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db import factory as _db_factory
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def mt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    # ASGITransport skips the lifespan, which is where ensure_schema() normally
    # bootstraps the SQLite tables (incl. context_documents/context_chunks that the
    # durable PostgresContextStore writes). Reset the cached process engine so it
    # picks up this test's SAGEWAI_HOME, then create the schema as the lifespan does.
    _db_factory.reset_engine()
    await _db_factory.ensure_schema()

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)
    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)
    member_b = await store.create_user(oid, "mb@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member_b["id"], "project:member", project_id=pb)

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")

    from sagewai.admin.serve import create_admin_serve_app, setup_memory_engines

    app = create_admin_serve_app(sf, identity_store=store)
    # ASGITransport skips the lifespan, so attach the per-project memory engine
    # resolver (durable backends) the same way the lifespan does, and bind the
    # audit store on the SAME engine (else a successful write fail-closes to 503).
    setup_memory_engines(app)
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit

    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
        "sess_member_b": await store.issue_session(oid, member_b["id"]),
    }
    await engine.dispose()
    # Release + clear the process engine so a durable backend never leaks into a
    # subsequent test (mirrors the lifespan teardown).
    await _db_factory.dispose_engine()


async def _req(app, method, path, *, token, project=None, json=None):
    headers = {"authorization": f"Bearer {token}"}
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


# ── Vector memory ────────────────────────────────────────────────────


async def test_vector_isolation_across_projects(mt_app):
    app = mt_app["app"]
    # PB ingests a doc through its own session.
    ing = await _req(
        app, "POST", "/api/v1/memory/vector/ingest",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"content": "pb-secret quarterly revenue figures"},
    )
    assert ing.status_code == 200

    # PB sees it (stats + search).
    pb_stats = await _req(
        app, "GET", "/api/v1/memory/vector/stats",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert pb_stats.json()["documents"] >= 1
    pb_search = await _req(
        app, "POST", "/api/v1/memory/vector/search",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"query": "pb-secret quarterly revenue", "top_k": 5},
    )
    assert any("pb-secret" in r["content"] for r in pb_search.json()["results"])

    # PA does NOT see it — stats empty, search empty.
    pa_stats = await _req(
        app, "GET", "/api/v1/memory/vector/stats",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_stats.json()["documents"] == 0
    pa_search = await _req(
        app, "POST", "/api/v1/memory/vector/search",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"query": "pb-secret quarterly revenue", "top_k": 5},
    )
    assert pa_search.json()["results"] == []


async def test_vector_forged_project_header_is_a_noop(mt_app):
    app = mt_app["app"]
    # PB ingests.
    await _req(
        app, "POST", "/api/v1/memory/vector/ingest",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"content": "pb-only forged-header probe content"},
    )
    # A PA session FORGES X-Project-ID: pb — the middleware re-resolves tenancy
    # from the session (PA), so this reads PA's empty scope, not PB's.
    forged = await _req(
        app, "POST", "/api/v1/memory/vector/search",
        token=mt_app["sess_member"], project=mt_app["pb"],
        json={"query": "pb-only forged-header probe", "top_k": 5},
    )
    # Either a 404 (forged/foreign project hidden) or an empty PA-scoped read —
    # never PB's content.
    if forged.status_code == 200:
        assert all("pb-only" not in r["content"] for r in forged.json()["results"])
    else:
        assert forged.status_code == 404


async def test_vector_viewer_cannot_ingest_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/memory/vector/ingest",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"content": "viewer should be denied"},
    )
    assert r.status_code == 403


async def test_vector_member_can_ingest_and_read_own(mt_app):
    app = mt_app["app"]
    ing = await _req(
        app, "POST", "/api/v1/memory/vector/ingest",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"content": "pa-owned content the member ingested"},
    )
    assert ing.status_code == 200
    search = await _req(
        app, "POST", "/api/v1/memory/vector/search",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"query": "pa-owned content", "top_k": 5},
    )
    assert any("pa-owned" in r["content"] for r in search.json()["results"])


# ── Graph memory ─────────────────────────────────────────────────────


async def test_graph_isolation_across_projects(mt_app):
    app = mt_app["app"]
    # PB creates an entity + relation.
    await _req(
        app, "POST", "/api/v1/memory/graph/entity",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"name": "PBEntity", "metadata": {"owner": "pb"}},
    )
    await _req(
        app, "POST", "/api/v1/memory/graph/entity",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"name": "PBOther"},
    )
    await _req(
        app, "POST", "/api/v1/memory/graph/relation",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
        json={"source": "PBEntity", "relation": "links", "target": "PBOther"},
    )

    # PB sees its graph.
    pb_stats = await _req(
        app, "GET", "/api/v1/memory/graph/stats",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert pb_stats.json()["entities"] >= 2
    assert pb_stats.json()["relations"] >= 1
    pb_get = await _req(
        app, "GET", "/api/v1/memory/graph/entity/PBEntity",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert pb_get.status_code == 200

    # PA sees an EMPTY graph; PB's entity is 404 in PA scope.
    pa_stats = await _req(
        app, "GET", "/api/v1/memory/graph/stats",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_stats.json()["entities"] == 0
    assert pa_stats.json()["relations"] == 0
    pa_ents = await _req(
        app, "GET", "/api/v1/memory/graph/entities",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_ents.json()["entities"] == []
    pa_get = await _req(
        app, "GET", "/api/v1/memory/graph/entity/PBEntity",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_get.status_code == 404


async def test_graph_viewer_cannot_create_entity_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/memory/graph/entity",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"name": "Denied"},
    )
    assert r.status_code == 403


async def test_graph_member_can_crud_own(mt_app):
    app = mt_app["app"]
    c = await _req(
        app, "POST", "/api/v1/memory/graph/entity",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"name": "PAThing", "metadata": {"k": "v"}},
    )
    assert c.status_code == 200
    g = await _req(
        app, "GET", "/api/v1/memory/graph/entity/PAThing",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert g.status_code == 200
    assert g.json()["metadata"].get("k") == "v"


# ── Context engine ───────────────────────────────────────────────────


async def test_context_isolation_across_projects(mt_app):
    app = mt_app["app"]
    # PB ingests a context document via its engine (write surface for context is
    # the ingest helper used by the routes; drive it through the resolver the way
    # a route does, scoped to PB).
    from sagewai.context.models import ContextScope, ContextSource

    resolver = app.state.memory_engines
    pb_engine = resolver.context_for(mt_app["pb"])
    await pb_engine.ingest_text(
        text="PB confidential context: project beta roadmap details.",
        title="PB Roadmap",
        scope=ContextScope.PROJECT,
        scope_id=pb_engine.project_id,
        source=ContextSource.MANUAL,
    )

    # PB sees it.
    pb_stats = await _req(
        app, "GET", "/api/v1/context/stats",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert pb_stats.json()["documents"] >= 1
    pb_docs = await _req(
        app, "GET", "/api/v1/context/documents",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert "PB Roadmap" in {d["title"] for d in pb_docs.json()["documents"]}

    # PA does NOT.
    pa_stats = await _req(
        app, "GET", "/api/v1/context/stats",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_stats.json()["documents"] == 0
    pa_docs = await _req(
        app, "GET", "/api/v1/context/documents",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert pa_docs.json()["documents"] == []
    pa_search = await _req(
        app, "POST", "/api/v1/context/search",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"query": "project beta roadmap", "top_k": 5},
    )
    assert pa_search.json()["results"] == []


async def test_context_durable_store_is_project_scoped(mt_app):
    """The context engine's metadata store is the durable Postgres/SQLite store,
    and PA's engine and PB's engine read disjoint project scopes from it."""
    from sagewai.context.pg_store import PostgresContextStore

    resolver = mt_app["app"].state.memory_engines
    pa_engine = resolver.context_for(mt_app["pa"])
    pb_engine = resolver.context_for(mt_app["pb"])
    assert isinstance(pa_engine.metadata_store, PostgresContextStore)
    assert pa_engine.project_id == mt_app["pa"]
    assert pb_engine.project_id == mt_app["pb"]


# ── Single-org guard ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def single_org_app(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    token = sf.validate_login("a@acme.io", "pw123456")["access_token"]

    from sagewai.admin.serve import create_admin_serve_app, setup_memory_engines

    app = create_admin_serve_app(sf)
    setup_memory_engines(app)
    yield {"app": app, "token": token}


async def test_single_org_memory_unchanged(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]
    # Ingest + search + stats all work under the implicit "default" project.
    ing = await _req(
        app, "POST", "/api/v1/memory/vector/ingest", token=token,
        json={"content": "single org default bucket content"},
    )
    assert ing.status_code == 200
    stats = await _req(app, "GET", "/api/v1/memory/vector/stats", token=token)
    assert stats.json()["documents"] >= 1
    search = await _req(
        app, "POST", "/api/v1/memory/vector/search", token=token,
        json={"query": "single org default", "top_k": 5},
    )
    assert any("single org" in r["content"] for r in search.json()["results"])

    # Graph + context still serve real data too.
    e = await _req(
        app, "POST", "/api/v1/memory/graph/entity", token=token,
        json={"name": "SoloEntity"},
    )
    assert e.status_code == 200
    g = await _req(app, "GET", "/api/v1/memory/graph/stats", token=token)
    assert g.json()["entities"] >= 1
