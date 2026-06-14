# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Analytics list endpoints return the CursorPage envelope, not bare arrays.

Regression: the admin UI reads `page.items` from a typed CursorPage<T>
({ items, next_cursor, has_more }). A bare array makes `page.items` undefined
→ the page's `.map()` crashes (safety/audit, observability/prompts). The dash-
board's runs/sessions already return the envelope; audit-events and prompt-logs
did not. These tests pin both to the envelope shape and assert the stored audit
shape is mapped onto the UI's AuditEvent contract (agent_name / detail /
created_at), so rows render populated instead of blank.
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
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


def _assert_cursorpage(body: object) -> None:
    assert isinstance(body, dict), "must be a CursorPage object, not a bare array"
    assert isinstance(body.get("items"), list), "items must be a list"
    assert "next_cursor" in body
    assert "has_more" in body


@pytest.mark.asyncio
async def test_audit_events_is_cursorpage(client):
    r = await client.get("/api/v1/audit/events")
    assert r.status_code == 200, r.text
    _assert_cursorpage(r.json())


@pytest.mark.asyncio
async def test_prompt_logs_is_cursorpage(client):
    r = await client.get("/api/v1/prompts/logs")
    assert r.status_code == 200, r.text
    _assert_cursorpage(r.json())


@pytest.mark.asyncio
async def test_audit_events_mapped_to_ui_contract(client, state_path):
    """A stored admin audit event surfaces with the UI's field names."""
    from sagewai.admin.audit import emit_audit
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    emit_audit(
        sf,
        event_type="token.created",
        actor_label="admin@acme",
        target="api-token-42",
        details={"agent_name": "billing-bot"},
    )

    r = await client.get("/api/v1/audit/events")
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_cursorpage(body)
    assert len(body["items"]) == 1
    evt = body["items"][0]
    # The UI's AuditEvent contract — every column the table renders.
    assert set(evt) == {"id", "agent_name", "event_type", "detail", "created_at"}
    assert evt["event_type"] == "token.created"
    assert evt["agent_name"] == "billing-bot"  # details.agent_name wins
    assert evt["detail"] == "api-token-42"  # target
    assert evt["created_at"]  # the stored ts, not blank
