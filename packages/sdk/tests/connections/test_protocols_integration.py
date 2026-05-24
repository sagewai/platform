# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: plugin validation + ConnectionStore round-trip."""
from __future__ import annotations

from sagewai.connections.protocols import (
    DEFAULT_KEY_FOR,
    PROTOCOLS,
    get_protocol,
)
from sagewai.connections.store import ConnectionStore


def _store(tmp_path):
    return ConnectionStore(
        tmp_path / "s.json",
        allowed_protocols=tuple(p.id for p in PROTOCOLS),
        default_key_for=DEFAULT_KEY_FOR,
    )


def test_create_oauth2_validates_against_plugin_schema(tmp_path):
    store = _store(tmp_path)
    plugin = get_protocol("oauth2")
    pd = {
        "provider": "spotify",
        "client_id": "cid", "client_secret": "csec",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": [],
        "tokens": None,
    }
    # Plugin-side validation (the generic CRUD route in PR4 will run this
    # before store.create; we run it inline here):
    plugin.protocol_data_schema().model_validate(pd)
    # Generic store create works (it's opaque to the schema):
    c = store.create(
        protocol="oauth2", project_id="default", display_name="Spotify",
        tags=["music"], protocol_data=pd,
    )
    assert c.id.startswith("conn_oauth2_")
    assert c.is_default is True


def test_oauth2_default_key_isolates_providers(tmp_path):
    """With DEFAULT_KEY_FOR wired, spotify and google clients default independently."""
    store = _store(tmp_path)
    sp = store.create(
        protocol="oauth2", project_id="default", display_name="Spotify",
        tags=[], protocol_data={
            "provider": "spotify", "client_id": "c", "client_secret": "s",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": ["x"], "granted_scopes": [], "tokens": None,
        },
    )
    g = store.create(
        protocol="oauth2", project_id="default", display_name="Google",
        tags=[], protocol_data={
            "provider": "google", "client_id": "c", "client_secret": "s",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": ["openid"], "granted_scopes": [], "tokens": None,
        },
    )
    # Both default — different providers
    assert sp.is_default is True
    assert g.is_default is True


def test_inference_default_key_isolates_provider_keys(tmp_path):
    """With DEFAULT_KEY_FOR wired, runpod and modal default independently."""
    store = _store(tmp_path)
    rp = store.create(
        protocol="inference", project_id="default", display_name="RunPod",
        tags=[], protocol_data={
            "provider_key": "runpod",
            "secrets": {"RUNPOD_API_KEY": "x"},
        },
    )
    mo = store.create(
        protocol="inference", project_id="default", display_name="Modal",
        tags=[], protocol_data={
            "provider_key": "modal",
            "secrets": {"MODAL_TOKEN_ID": "x", "MODAL_TOKEN_SECRET": "y"},
        },
    )
    assert rp.is_default is True
    assert mo.is_default is True


def test_http_plugin_round_trip(tmp_path):
    store = _store(tmp_path)
    plugin = get_protocol("http")
    pd = {
        "base_url": "https://api.example.com",
        "auth": {"kind": "bearer"},
        "operations": {"ping": {"method": "GET", "path": "/ping"}},
    }
    plugin.protocol_data_schema().model_validate(pd)
    c = store.create(
        protocol="http", project_id="default", display_name="Example",
        tags=["custom"], protocol_data=pd,
    )
    assert c.protocol == "http"


def test_sdk_plugin_round_trip(tmp_path):
    store = _store(tmp_path)
    plugin = get_protocol("sdk")
    pd = {
        "entrypoint": "sagewai.tools.builtins.paypal:paypal_api",
        "credential_fields": [
            {"name": "PAYPAL_CLIENT_ID", "label": "Client ID", "type": "password"},
        ],
        "secrets": {"PAYPAL_CLIENT_ID": "x"},
    }
    plugin.protocol_data_schema().model_validate(pd)
    c = store.create(
        protocol="sdk", project_id="default", display_name="PayPal",
        tags=["payments"], protocol_data=pd,
    )
    masked = plugin.public_view(c.protocol_data)
    assert masked["secrets"]["PAYPAL_CLIENT_ID"] == "***"


def test_mcp_plugin_round_trip(tmp_path):
    store = _store(tmp_path)
    plugin = get_protocol("mcp")
    pd = {"transport": "stdio", "command": ["mcp-server-filesystem"]}
    plugin.protocol_data_schema().model_validate(pd)
    c = store.create(
        protocol="mcp", project_id="default", display_name="filesystem",
        tags=["dev"], protocol_data=pd,
    )
    assert c.protocol == "mcp"
