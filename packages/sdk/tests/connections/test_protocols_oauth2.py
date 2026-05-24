# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth2 plugin tests."""
from __future__ import annotations

import pytest
from fastapi import APIRouter
from pydantic import ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.protocols.oauth2 import (
    OAuth2ProtocolData,
    OAuth2ProtocolPlugin,
    oauth2_default_key,
)
from sagewai.connections.store import ConnectionStore


def _conn(protocol_data, display_name="Test"):
    return Connection(
        id="conn_oauth2_x", protocol="oauth2", project_id="default",
        display_name=display_name, tags=(), credentials_backend=None,
        status="pending", last_tested_at=None, last_test_ok=None,
        is_default=False, created_at="t", updated_at="t",
        last_error=None, protocol_data=protocol_data,
    )


def test_plugin_identity():
    p = OAuth2ProtocolPlugin()
    assert p.id == "oauth2"
    assert p.display_name == "OAuth 2.0"
    assert set(p.sensitive_fields) == {
        "client_secret", "tokens.access_token", "tokens.refresh_token",
    }


def test_schema_accepts_minimal_pending_record():
    p = OAuth2ProtocolPlugin()
    valid = {
        "provider": "spotify",
        "client_id": "cid",
        "client_secret": "csec",
        "redirect_uri": "http://localhost:8080/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": [],
        "tokens": None,
    }
    p.protocol_data_schema().model_validate(valid)


def test_schema_accepts_full_authorized_record():
    p = OAuth2ProtocolPlugin()
    valid = {
        "provider": "spotify",
        "client_id": "cid",
        "client_secret": "csec",
        "redirect_uri": "http://localhost:8080/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": ["user-read-private"],
        "tokens": {
            "access_token": "at",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "expires_at": "2026-05-24T15:00:00+00:00",
            "obtained_at": "2026-05-24T14:00:00+00:00",
            "last_refreshed_at": None,
        },
    }
    p.protocol_data_schema().model_validate(valid)


def test_schema_rejects_unknown_provider():
    p = OAuth2ProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({
            "provider": "not-a-provider",
            "client_id": "cid", "client_secret": "csec",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": ["s1"], "granted_scopes": [], "tokens": None,
        })


def test_schema_rejects_empty_requested_scopes():
    p = OAuth2ProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({
            "provider": "spotify", "client_id": "cid", "client_secret": "csec",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": [], "granted_scopes": [], "tokens": None,
        })


def test_oauth2_default_key_extracts_provider():
    assert oauth2_default_key({"provider": "spotify"}) == "spotify"
    assert oauth2_default_key({}) is None


def test_public_view_masks_client_secret():
    p = OAuth2ProtocolPlugin()
    data = {
        "provider": "spotify", "client_id": "cid", "client_secret": "real-secret",
        "redirect_uri": "http://localhost/cb", "requested_scopes": ["s"],
        "granted_scopes": [], "tokens": None,
    }
    view = p.public_view(data)
    assert view["client_secret"] == "***"
    assert view["client_id"] == "cid"  # not masked


def test_public_view_masks_tokens_when_present():
    p = OAuth2ProtocolPlugin()
    data = {
        "provider": "spotify", "client_id": "cid", "client_secret": "csec",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["s"], "granted_scopes": ["s"],
        "tokens": {
            "access_token": "real-at", "refresh_token": "real-rt",
            "token_type": "Bearer", "expires_at": "t1", "obtained_at": "t0",
            "last_refreshed_at": None,
        },
    }
    view = p.public_view(data)
    assert view["tokens"]["access_token"] == "***"
    assert view["tokens"]["refresh_token"] == "***"
    assert view["tokens"]["token_type"] == "Bearer"  # non-sensitive preserved


def test_public_view_include_secrets_returns_plain():
    p = OAuth2ProtocolPlugin()
    data = {
        "provider": "spotify", "client_id": "cid", "client_secret": "real",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["s"], "granted_scopes": [], "tokens": None,
    }
    view = p.public_view(data, include_secrets=True)
    assert view["client_secret"] == "real"


def test_extra_routes_returns_router_with_4_subroutes():
    p = OAuth2ProtocolPlugin()
    router = p.extra_routes()
    assert isinstance(router, APIRouter)
    paths = {r.path for r in router.routes}
    # Routes are mounted by PR4 at /api/v1/admin/connections/oauth2 + these:
    assert "/{connection_id}/start" in paths
    assert "/callback" in paths
    assert "/{connection_id}/refresh" in paths
    assert "/{connection_id}/revoke" in paths


def test_extra_cli_returns_5_commands():
    p = OAuth2ProtocolPlugin()
    cmds = p.extra_cli()
    names = {c.name for c in cmds}
    assert {"start", "refresh", "revoke", "reauthorize", "providers"}.issubset(names)


@pytest.mark.asyncio
async def test_on_create_passes_through(tmp_path):
    """on_create is currently a no-op; PR2 doesn't auto-derive redirect_uri."""
    p = OAuth2ProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({
        "provider": "spotify", "client_id": "c", "client_secret": "s",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["s1"], "granted_scopes": [], "tokens": None,
    })
    result = await p.on_create(conn, ctx=ctx)
    assert result is conn


@pytest.mark.asyncio
async def test_test_method_returns_not_ok_when_tokens_missing(tmp_path):
    p = OAuth2ProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({
        "provider": "spotify", "client_id": "c", "client_secret": "s",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["s1"], "granted_scopes": [], "tokens": None,
    })
    result = await p.test(conn, ctx=ctx)
    assert result.ok is False
    assert "not authorized" in (result.message or "").lower() or "no token" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_test_method_returns_ok_when_tokens_present_and_not_expired(tmp_path):
    """A connection with a non-expired access token tests ok without hitting the network."""
    p = OAuth2ProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn = _conn({
        "provider": "spotify", "client_id": "c", "client_secret": "s",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["s1"], "granted_scopes": ["s1"],
        "tokens": {
            "access_token": "AT", "refresh_token": "RT", "token_type": "Bearer",
            "expires_at": future, "obtained_at": future, "last_refreshed_at": None,
        },
    })
    result = await p.test(conn, ctx=ctx)
    assert result.ok is True
