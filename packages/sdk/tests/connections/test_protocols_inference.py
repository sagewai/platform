# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Inference plugin tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.protocols.inference import (
    InferenceProtocolPlugin,
    inference_default_key,
)
from sagewai.connections.store import ConnectionStore


def _conn(protocol_data, project_id="default", display_name="X"):
    return Connection(
        id=f"conn_inference_{display_name.lower()}", protocol="inference",
        project_id=project_id, display_name=display_name,
        tags=(), credentials_backend=None, status="pending",
        last_tested_at=None, last_test_ok=None, is_default=False,
        created_at="t", updated_at="t", last_error=None,
        protocol_data=protocol_data,
    )


def test_plugin_identity():
    p = InferenceProtocolPlugin()
    assert p.id == "inference"
    assert p.display_name == "Inference provider"


def test_protocol_data_schema_accepts_known_provider_key():
    p = InferenceProtocolPlugin()
    valid = {
        "provider_key": "runpod",
        "secrets": {"RUNPOD_API_KEY": "rp_xxx"},
    }
    p.protocol_data_schema().model_validate(valid)


def test_protocol_data_schema_accepts_custom_with_base_url():
    p = InferenceProtocolPlugin()
    valid = {
        "provider_key": "custom",
        "base_url": "https://my-endpoint.example",
        "secrets": {"CUSTOM_API_KEY": "x"},
    }
    p.protocol_data_schema().model_validate(valid)


def test_protocol_data_schema_rejects_unknown_provider_key():
    p = InferenceProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({
            "provider_key": "not-a-provider",
            "secrets": {},
        })


def test_inference_default_key_extracts_provider_key():
    assert inference_default_key({"provider_key": "modal"}) == "modal"
    assert inference_default_key({}) is None


def test_public_view_masks_secret_values():
    p = InferenceProtocolPlugin()
    data = {
        "provider_key": "runpod",
        "secrets": {"RUNPOD_API_KEY": "rp_real"},
    }
    view = p.public_view(data)
    assert view["secrets"]["RUNPOD_API_KEY"] == "***"


def test_public_view_include_secrets_returns_plain():
    p = InferenceProtocolPlugin()
    data = {
        "provider_key": "runpod",
        "secrets": {"RUNPOD_API_KEY": "rp_real"},
    }
    view = p.public_view(data, include_secrets=True)
    assert view["secrets"]["RUNPOD_API_KEY"] == "rp_real"


def test_extra_routes_returns_empty_router():
    assert InferenceProtocolPlugin().extra_routes().routes == []


def test_extra_cli_has_test_command():
    cmds = InferenceProtocolPlugin().extra_cli()
    names = {c.name for c in cmds}
    assert "test" in names


@pytest.mark.asyncio
async def test_test_method_dispatches_to_probe(monkeypatch, tmp_path):
    """test() calls into provider_probes via the existing dispatcher."""
    called = {}

    async def _stub_probe(provider_key: str, secrets: dict, base_url: str | None):
        called["provider_key"] = provider_key
        called["secrets"] = secrets
        called["base_url"] = base_url
        return {"ok": True, "detail": "probed ok"}

    monkeypatch.setattr(
        "sagewai.connections.protocols.inference._run_probe", _stub_probe
    )
    p = InferenceProtocolPlugin()
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({
        "provider_key": "runpod",
        "secrets": {"RUNPOD_API_KEY": "rp_x"},
    })
    result = await p.test(conn, ctx=ctx)
    assert result.ok is True
    assert called["provider_key"] == "runpod"
