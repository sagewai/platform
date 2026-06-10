# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Memory + context admin routes are wired to the REAL engines (single-org).

The 13 vector/graph/context endpoints were stubs returning empty/no-op shapes.
These tests build the real ``create_admin_serve_app`` in SINGLE-ORG mode, drive
the actual HTTP routes, and assert the data flows through the in-process engines
(VectorMemory / GraphMemory / ContextEngine) attached to ``app.state``.

``httpx.ASGITransport`` does not run the app lifespan, so the engines that the
lifespan would normally attach are bound here via the same setup helper the
lifespan uses (``_setup_memory_engines``). This mirrors the documented caveat in
the other real-route tests (the lifespan auto-wiring never fires under
ASGITransport).
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.serve import _setup_memory_engines, create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


@pytest_asyncio.fixture
async def single_org_app(tmp_path, monkeypatch):
    # SINGLE-ORG mode (the default — do NOT set SAGEWAI_TENANCY_MODE=multi).
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(
        org_name="Acme",
        admin_email="a@acme.io",
        admin_password="pw123456",
    )
    login = sf.validate_login("a@acme.io", "pw123456")
    token = login["access_token"]

    app = create_admin_serve_app(sf)
    # ASGITransport skips the lifespan, so attach the memory/context engines the
    # same way the lifespan does.
    _setup_memory_engines(app)

    yield {"app": app, "token": token}


async def _req(app, method, path, *, token, json=None):
    headers = {"authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


# ── Vector memory ────────────────────────────────────────────────────


async def test_vector_ingest_then_search_and_stats(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    # Empty to start.
    stats0 = await _req(app, "GET", "/api/v1/memory/vector/stats", token=token)
    assert stats0.status_code == 200
    assert stats0.json()["documents"] == 0

    # Ingest a document.
    ing = await _req(
        app, "POST", "/api/v1/memory/vector/ingest", token=token,
        json={"content": "the quick brown fox jumps over the lazy dog"},
    )
    assert ing.status_code == 200
    assert ing.json()["status"] == "ok"

    # Stats now reflect it.
    stats1 = await _req(app, "GET", "/api/v1/memory/vector/stats", token=token)
    assert stats1.status_code == 200
    body = stats1.json()
    assert body["documents"] >= 1
    assert "backend" in body

    # Search returns the stored content.
    res = await _req(
        app, "POST", "/api/v1/memory/vector/search", token=token,
        json={"query": "quick brown fox", "top_k": 5},
    )
    assert res.status_code == 200
    rbody = res.json()
    assert rbody["query"] == "quick brown fox"
    contents = [r["content"] for r in rbody["results"]]
    assert any("quick brown fox" in c for c in contents)
    assert rbody["count"] == len(rbody["results"])


# ── Graph memory ─────────────────────────────────────────────────────


async def test_graph_entity_relation_query_and_reads(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    stats0 = await _req(app, "GET", "/api/v1/memory/graph/stats", token=token)
    assert stats0.status_code == 200
    assert stats0.json()["entities"] == 0
    assert stats0.json()["relations"] == 0

    # Create two entities.
    e1 = await _req(
        app, "POST", "/api/v1/memory/graph/entity", token=token,
        json={"name": "Alice", "metadata": {"type": "person"}},
    )
    assert e1.status_code == 200
    assert e1.json()["entity"] == "Alice"
    e2 = await _req(
        app, "POST", "/api/v1/memory/graph/entity", token=token,
        json={"name": "Acme", "metadata": {"type": "company"}},
    )
    assert e2.status_code == 200

    # Relate them.
    rel = await _req(
        app, "POST", "/api/v1/memory/graph/relation", token=token,
        json={"source": "Alice", "relation": "works_at", "target": "Acme"},
    )
    assert rel.status_code == 200
    assert rel.json()["relation"]

    # Stats reflect entities + relation.
    stats1 = await _req(app, "GET", "/api/v1/memory/graph/stats", token=token)
    assert stats1.json()["entities"] >= 2
    assert stats1.json()["relations"] >= 1

    # List entities.
    ents = await _req(app, "GET", "/api/v1/memory/graph/entities", token=token)
    assert ents.status_code == 200
    names = {e["name"] for e in ents.json()["entities"]}
    assert {"Alice", "Acme"} <= names
    assert ents.json()["count"] >= 2

    # Get a single entity.
    ent = await _req(app, "GET", "/api/v1/memory/graph/entity/Alice", token=token)
    assert ent.status_code == 200
    assert ent.json()["name"] == "Alice"
    assert ent.json()["metadata"].get("type") == "person"

    # Neighbors.
    nbr = await _req(
        app, "GET", "/api/v1/memory/graph/entity/Alice/neighbors", token=token,
    )
    assert nbr.status_code == 200
    nbr_names = {n["entity"] for n in nbr.json()["neighbors"]}
    assert "Acme" in nbr_names

    # Relations.
    rels = await _req(
        app, "GET", "/api/v1/memory/graph/entity/Alice/relations", token=token,
    )
    assert rels.status_code == 200
    triples = {
        (r["source"], r["relation"], r["target"]) for r in rels.json()["relations"]
    }
    assert ("Alice", "works_at", "Acme") in triples

    # Query — seed entity name appears in the query string.
    q = await _req(
        app, "POST", "/api/v1/memory/graph/query", token=token,
        json={"query": "tell me about Alice", "top_k": 5},
    )
    assert q.status_code == 200
    qbody = q.json()
    assert qbody["count"] == len(qbody["results"])
    joined = " ".join(r["content"] for r in qbody["results"])
    assert "Alice" in joined


# ── Context engine ───────────────────────────────────────────────────


async def test_context_ingest_then_search_documents_and_stats(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    # Ingest text through the real ContextEngine attached to app.state.
    engine = app.state.context_engine
    from sagewai.context.models import ContextScope, ContextSource

    await engine.ingest_text(
        text="Quarterly revenue rose twenty percent driven by enterprise sales.",
        title="Q3 Report",
        scope=ContextScope.PROJECT,
        scope_id=engine.project_id,
        source=ContextSource.MANUAL,
    )

    # Stats reflect the ingested document + chunks.
    stats = await _req(app, "GET", "/api/v1/context/stats", token=token)
    assert stats.status_code == 200
    sbody = stats.json()
    assert sbody["documents"] >= 1
    assert sbody["chunks"] >= 1

    # Scopes enumerate the two ContextScope values with per-scope counts.
    scopes = await _req(app, "GET", "/api/v1/context/scopes", token=token)
    assert scopes.status_code == 200
    scope_rows = scopes.json()["scopes"]
    by_scope = {row["scope"]: row["document_count"] for row in scope_rows}
    assert by_scope.get("project", 0) >= 1

    # Documents list reflects the ingested doc.
    docs = await _req(app, "GET", "/api/v1/context/documents", token=token)
    assert docs.status_code == 200
    dbody = docs.json()
    titles = {d["title"] for d in dbody["documents"]}
    assert "Q3 Report" in titles
    assert dbody["count"] >= 1

    # Search returns the chunk.
    res = await _req(
        app, "POST", "/api/v1/context/search", token=token,
        json={"query": "quarterly revenue", "top_k": 5},
    )
    assert res.status_code == 200
    rbody = res.json()
    assert rbody["query"] == "quarterly revenue"
    assert rbody["count"] == len(rbody["results"])
    joined = " ".join(r["content"] for r in rbody["results"])
    assert "revenue" in joined.lower()
