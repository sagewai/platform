# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""POST /api/v1/context/documents — file upload + paste-text ingestion.

Regression: the admin API only exposed GET on /api/v1/context/documents, so
the Workbench "Add Knowledge" dialog (which POSTs there) failed with 405. These
tests cover the upload + text routes that wire the dialog to the existing
ContextEngine ingestion pipeline (default in-process HashEmbedder, no API key).
"""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    from sagewai.admin.state_file import AdminStateFile

    path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "default_admin_state_path", lambda: path)
    return path


@pytest.fixture
async def client(state_path):
    from sagewai.admin.serve import create_admin_serve_app, setup_memory_engines
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    # The admin lifespan wires the (zero-dep, in-process) memory engines at
    # startup; httpx.ASGITransport doesn't run the lifespan, so do it directly.
    setup_memory_engines(app)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_post_documents_is_not_405(client):
    # The route used to be GET-only — POST returned 405 Method Not Allowed.
    r = await client.post(
        "/api/v1/context/documents",
        files={"file": ("x.txt", b"hi", "text/plain")},
        data={"scope": "org"},
    )
    assert r.status_code != 405, "POST /api/v1/context/documents must be handled"


@pytest.mark.asyncio
async def test_upload_context_document_ingests_file(client):
    before = (await client.get("/api/v1/context/documents")).json()["total"]
    r = await client.post(
        "/api/v1/context/documents",
        files={
            "file": (
                "note.md",
                b"# Note\nSagewai upload regression test. keyword: feistel.\n",
                "text/markdown",
            )
        },
        data={"scope": "org", "scope_id": "", "enable_graph": "false"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "ok"
    after = (await client.get("/api/v1/context/documents")).json()["total"]
    assert after == before + 1


@pytest.mark.asyncio
async def test_ingest_context_text(client):
    r = await client.post(
        "/api/v1/context/documents/text",
        json={"text": "Sagewai paste-text regression.", "title": "PastedNote", "scope": "org"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ingest_context_text_rejects_empty(client):
    r = await client.post(
        "/api/v1/context/documents/text",
        json={"text": "   ", "title": "Empty", "scope": "org"},
    )
    assert r.status_code == 422
