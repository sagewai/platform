# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for /api/v1/admin/connections/tools/* routes."""
from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.tools import registry as tool_registry


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_app(tmp_path, monkeypatch=None):
    """Create a test app with a fresh state file.

    The connections_routes module uses SAGEWAI_ADMIN_STATE_FILE to locate
    its own separate store, and also needs SAGEWAI_MASTER_KEY for Sealed.Crypto.
    """
    state_path = tmp_path / "state.json"
    store_dir = tmp_path  # store will land at tmp_path/inference-providers.json

    if monkeypatch is not None:
        monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_path))
        monkeypatch.setenv("SAGEWAI_MASTER_KEY", "0QX41VWR6wFUCZbMFPVd2jUmd6O78TfqP02Jjpcgw_o=")
    else:
        os.environ["SAGEWAI_ADMIN_STATE_FILE"] = str(state_path)
        os.environ["SAGEWAI_MASTER_KEY"] = "0QX41VWR6wFUCZbMFPVd2jUmd6O78TfqP02Jjpcgw_o="

    sf = AdminStateFile(state_path)
    app = create_admin_serve_app(sf)
    headers = {"X-Project-ID": "acme"}
    return app, sf, headers


# ── Registry endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_endpoint_returns_api_key_tier(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/v1/admin/connections/tools/registry",
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    ids = {entry["id"] for entry in data}
    assert {"post_to_slack", "discord_api", "email_send", "mailchimp_api"} <= ids
    # No-auth tools must NOT appear
    assert "fetch_url" not in ids
    slack_entry = next(e for e in data if e["id"] == "post_to_slack")
    assert any(f["name"] == "SLACK_BOT_TOKEN" for f in slack_entry["credential_fields"])


# ── PUT (upsert) ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_tool_creates_encrypted_record(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
            json={"credentials": {"SLACK_BOT_TOKEN": "xoxb-secret"}},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_id"] == "post_to_slack"
    assert body["kind"] == "tool"
    # Raw secret must NOT appear in the response body
    assert "xoxb-secret" not in json.dumps(body)
    assert "SLACK_BOT_TOKEN" not in json.dumps(body)


@pytest.mark.asyncio
async def test_put_tool_rejects_missing_credential_field(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
            json={"credentials": {}},
        )
    assert resp.status_code == 400
    assert "SLACK_BOT_TOKEN" in resp.text


@pytest.mark.asyncio
async def test_put_tool_rejects_no_auth_tool(tmp_path, monkeypatch):
    """fetch_url has no credential_fields — upsert must be rejected."""
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/v1/admin/connections/tools/fetch_url",
            headers=headers,
            json={"credentials": {}},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_tool_rejects_unknown_tool(tmp_path, monkeypatch):
    """Tool not in catalog → 404."""
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/v1/admin/connections/tools/no_such_tool",
            headers=headers,
            json={"credentials": {}},
        )
    assert resp.status_code == 404


# ── GET (single + list) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tool_returns_404_when_not_configured(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_tools_returns_configured_record(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.put(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
            json={"credentials": {"SLACK_BOT_TOKEN": "xoxb-secret"}},
        )
        resp = await c.get("/api/v1/admin/connections/tools", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert any(r["tool_id"] == "post_to_slack" for r in data)
    # Raw secret must not leak
    assert "xoxb-secret" not in json.dumps(data)


# ── DELETE ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_tool_removes_record(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.put(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
            json={"credentials": {"SLACK_BOT_TOKEN": "xoxb-secret"}},
        )
        del_resp = await c.delete(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
        )
        assert del_resp.status_code == 204
        get_resp = await c.get(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
        )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_tool_404_when_not_present(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.delete(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
        )
    assert resp.status_code == 404


# ── POST /test ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_post_tool_test_runs_test_endpoint(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)

    # First configure the tool
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.put(
            "/api/v1/admin/connections/tools/post_to_slack",
            headers=headers,
            json={"credentials": {"SLACK_BOT_TOKEN": "xoxb-real"}},
        )

    # Mock the Slack auth.test endpoint
    respx.post("https://slack.com/api/auth.test").respond(
        200, json={"ok": True, "url": "https://acme.slack.com", "team": "Acme"},
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/admin/connections/tools/post_to_slack/test",
            headers=headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_post_tool_test_for_tool_without_test_endpoint(tmp_path, monkeypatch):
    """email_send declares no test_endpoint — should succeed if decryption works."""
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.put(
            "/api/v1/admin/connections/tools/email_send",
            headers=headers,
            json={"credentials": {"EMAIL_API_KEY": "re_test", "EMAIL_PROVIDER": "resend"}},
        )
        resp = await c.post(
            "/api/v1/admin/connections/tools/email_send/test",
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_post_tool_test_404_when_not_configured(tmp_path, monkeypatch):
    tool_registry._reset()
    app, _, headers = _make_app(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/admin/connections/tools/post_to_slack/test",
            headers=headers,
        )
    assert resp.status_code == 404
