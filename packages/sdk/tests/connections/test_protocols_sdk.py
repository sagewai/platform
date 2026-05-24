# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SDK plugin tests."""
from __future__ import annotations

import pytest
from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.protocols.sdk import SdkProtocolPlugin
from sagewai.connections.store import ConnectionStore


def _conn(protocol_data):
    return Connection(
        id="conn_sdk_paypal", protocol="sdk", project_id="default",
        display_name="PayPal", tags=(), credentials_backend=None,
        status="pending", last_tested_at=None, last_test_ok=None,
        is_default=False, created_at="t", updated_at="t",
        last_error=None, protocol_data=protocol_data,
    )


def test_plugin_identity():
    p = SdkProtocolPlugin()
    assert p.id == "sdk"
    assert p.display_name == "SDK builtin"


def test_sensitive_fields_default_empty_then_derived_per_record():
    """Class-level sensitive_fields is empty; per-record derivation happens at masking time."""
    p = SdkProtocolPlugin()
    assert p.sensitive_fields == ()


def test_protocol_data_schema_accepts_valid_shape():
    p = SdkProtocolPlugin()
    valid = {
        "entrypoint": "sagewai.tools.builtins.paypal:paypal_api",
        "credential_fields": [
            {"name": "PAYPAL_CLIENT_ID", "label": "Client ID", "type": "password"},
            {"name": "PAYPAL_ENV", "label": "Environment", "type": "text"},
        ],
    }
    p.protocol_data_schema().model_validate(valid)


def test_protocol_data_schema_rejects_bad_entrypoint():
    p = SdkProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({
            "entrypoint": "no_colon_here",
            "credential_fields": [],
        })


def test_public_view_masks_password_fields():
    """Any credential_fields[].type == 'password' is masked."""
    p = SdkProtocolPlugin()
    data = {
        "entrypoint": "sagewai.tools.builtins.paypal:paypal_api",
        "credential_fields": [
            {"name": "PAYPAL_CLIENT_ID", "label": "Client ID", "type": "password"},
            {"name": "PAYPAL_ENV", "label": "Environment", "type": "text"},
        ],
        "secrets": {"PAYPAL_CLIENT_ID": "sk_real_value", "PAYPAL_ENV": "sandbox"},
    }
    view = p.public_view(data)
    assert view["secrets"]["PAYPAL_CLIENT_ID"] == "***"
    assert view["secrets"]["PAYPAL_ENV"] == "sandbox"


def test_public_view_include_secrets_returns_plain():
    p = SdkProtocolPlugin()
    data = {
        "entrypoint": "x:y",
        "credential_fields": [{"name": "K", "label": "K", "type": "password"}],
        "secrets": {"K": "shh"},
    }
    view = p.public_view(data, include_secrets=True)
    assert view["secrets"]["K"] == "shh"


def test_extra_routes_returns_empty_router():
    assert SdkProtocolPlugin().extra_routes().routes == []


def test_extra_cli_returns_empty_list():
    assert SdkProtocolPlugin().extra_cli() == []


@pytest.mark.asyncio
async def test_test_method_returns_ok(tmp_path):
    """SDK tools have no transport — test() is a no-op success."""
    p = SdkProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"entrypoint": "x:y", "credential_fields": [], "secrets": {}})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is True


@pytest.mark.asyncio
async def test_lifecycle_hooks_pass_through(tmp_path):
    p = SdkProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"entrypoint": "x:y", "credential_fields": [], "secrets": {}})
    assert await p.on_create(conn, ctx=ctx) is conn
    assert await p.on_update(conn, conn, ctx=ctx) is conn
    await p.on_delete(conn, ctx=ctx)
