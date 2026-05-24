# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HTTP plugin tests."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.protocols.http import HttpProtocolPlugin
from sagewai.connections.store import ConnectionStore


def _conn(protocol_data):
    return Connection(
        id="conn_http_x", protocol="http", project_id="default",
        display_name="X", tags=(), credentials_backend=None,
        status="pending", last_tested_at=None, last_test_ok=None,
        is_default=False, created_at="t", updated_at="t",
        last_error=None, protocol_data=protocol_data,
    )


def test_plugin_identity():
    p = HttpProtocolPlugin()
    assert p.id == "http"
    assert p.display_name == "HTTP / REST"
    assert p.sensitive_fields == ()


def test_protocol_data_schema_accepts_catalogued_form():
    p = HttpProtocolPlugin()
    schema = p.protocol_data_schema()
    assert issubclass(schema, BaseModel)
    valid = {
        "base_url": "https://api.spotify.com/v1",
        "auth": {"kind": "oauth2", "oauth_provider": "spotify"},
        "operations_ref": "spotify",
    }
    schema.model_validate(valid)  # does not raise


def test_protocol_data_schema_accepts_adhoc_form():
    p = HttpProtocolPlugin()
    valid = {
        "base_url": "https://api.example.com",
        "auth": {"kind": "bearer", "header": "Authorization", "prefix": "Bearer "},
        "operations": {"get_user": {"method": "GET", "path": "/users/me"}},
    }
    p.protocol_data_schema().model_validate(valid)


def test_protocol_data_schema_rejects_missing_base_url():
    p = HttpProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({"auth": {"kind": "none"}})


def test_protocol_data_schema_rejects_missing_auth():
    p = HttpProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({"base_url": "https://x.com"})


def test_public_view_no_sensitive_fields_stripped():
    p = HttpProtocolPlugin()
    data = {"base_url": "https://api.x", "auth": {"kind": "none"}}
    assert p.public_view(data) == data


def test_extra_routes_returns_empty_router():
    p = HttpProtocolPlugin()
    router = p.extra_routes()
    assert isinstance(router, APIRouter)
    assert router.routes == []


def test_extra_cli_returns_empty_list():
    assert HttpProtocolPlugin().extra_cli() == []


@respx.mock
@pytest.mark.asyncio
async def test_test_method_returns_ok_on_2xx(tmp_path):
    p = HttpProtocolPlugin()
    respx.head("https://api.spotify.com/v1").mock(return_value=httpx.Response(200))
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"base_url": "https://api.spotify.com/v1", "auth": {"kind": "none"}})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is True
    assert result.status_code == 200


@respx.mock
@pytest.mark.asyncio
async def test_test_method_returns_not_ok_on_5xx(tmp_path):
    p = HttpProtocolPlugin()
    respx.head("https://api.example.com").mock(return_value=httpx.Response(503))
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"base_url": "https://api.example.com", "auth": {"kind": "none"}})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is False
    assert result.status_code == 503


@respx.mock
@pytest.mark.asyncio
async def test_test_method_handles_network_error(tmp_path):
    p = HttpProtocolPlugin()
    respx.head("https://unreachable.example").mock(side_effect=httpx.ConnectError("dns"))
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"base_url": "https://unreachable.example", "auth": {"kind": "none"}})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is False
    assert result.message is not None
    assert "dns" in result.message.lower() or "connect" in result.message.lower()


@pytest.mark.asyncio
async def test_lifecycle_hooks_pass_through(tmp_path):
    p = HttpProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"base_url": "https://api.x", "auth": {"kind": "none"}})
    # on_create / on_update / on_delete: no-op for http plugin
    after_create = await p.on_create(conn, ctx=ctx)
    assert after_create is conn
    after_update = await p.on_update(conn, conn, ctx=ctx)
    assert after_update is conn
    await p.on_delete(conn, ctx=ctx)  # returns None, raises nothing
