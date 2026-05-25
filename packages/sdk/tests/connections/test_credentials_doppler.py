# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DopplerBackend — schemas + identity + behavioral tests."""
from __future__ import annotations

import httpx
import pytest
import respx

from sagewai.connections.credentials.doppler import (
    DopplerBackend,
    DopplerBackendConfig,
)
from sagewai.connections.credentials.errors import (
    DopplerApiError,
    DopplerAuthError,
    DopplerConfigError,
)


def test_identity():
    b = DopplerBackend()
    assert b.id == "doppler"
    assert b.display_name == "Doppler"


def test_schema_accepts_valid_config():
    cfg = DopplerBackendConfig.model_validate({
        "service_token": "dp.st.dev.abc123abc",
        "project": "sagewai",
        "config": "prd",
        "name_prefix": "SPOTIFY_MARKETING",
    })
    assert cfg.service_token.startswith("dp.st.")
    assert cfg.project == "sagewai"
    assert cfg.base_url == "https://api.doppler.com"  # default


def test_schema_accepts_custom_base_url():
    cfg = DopplerBackendConfig.model_validate({
        "service_token": "dp.st.dev.xyz",
        "project": "p", "config": "c", "name_prefix": "PFX",
        "base_url": "https://doppler.internal/api",
    })
    assert cfg.base_url == "https://doppler.internal/api"


def test_schema_rejects_token_without_prefix():
    with pytest.raises(Exception):  # Pydantic ValidationError
        DopplerBackendConfig.model_validate({
            "service_token": "not-a-doppler-token",
            "project": "p", "config": "c", "name_prefix": "PFX",
        })


def test_schema_rejects_invalid_name_prefix():
    """name_prefix must be UPPER_SNAKE."""
    with pytest.raises(Exception):
        DopplerBackendConfig.model_validate({
            "service_token": "dp.st.dev.x",
            "project": "p", "config": "c", "name_prefix": "lower-case",
        })


def test_schema_rejects_missing_required_fields():
    with pytest.raises(Exception):
        DopplerBackendConfig.model_validate({
            "service_token": "dp.st.dev.x",
            "name_prefix": "PFX",
        })  # missing project + config


def test_schema_rejects_extra_fields():
    with pytest.raises(Exception):
        DopplerBackendConfig.model_validate({
            "service_token": "dp.st.dev.x",
            "project": "p", "config": "c", "name_prefix": "PFX",
            "unknown_field": "value",
        })


def test_validate_config_raises_DopplerConfigError_on_bad_input():
    b = DopplerBackend()
    with pytest.raises(DopplerConfigError):
        b.validate_config({"project": "p"})  # missing everything else


# ─── Behavioral tests using respx ─────────────────────────────────────


@respx.mock
def test_encrypt_replaces_leaves_with_doppler_marker():
    b = DopplerBackend()
    data = {"client_secret": "real-secret", "client_id": "cid"}
    config = {
        "service_token": "dp.st.dev.abc",
        "project": "sagewai", "config": "prd",
        "name_prefix": "SPOTIFY_MARKETING",
    }
    encrypted = b.encrypt_fields(
        data,
        sensitive_field_paths=("client_secret",),
        backend_config=config,
    )
    assert encrypted["client_secret"] == {"$doppler": {
        "name": "SPOTIFY_MARKETING_CLIENT_SECRET",
    }}
    assert encrypted["client_id"] == "cid"


@respx.mock
def test_encrypt_derives_nested_path_with_underscores():
    b = DopplerBackend()
    data = {"tokens": {"access_token": "AT", "refresh_token": "RT"}}
    config = {
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c",
        "name_prefix": "PFX",
    }
    encrypted = b.encrypt_fields(
        data,
        sensitive_field_paths=("tokens.access_token", "tokens.refresh_token"),
        backend_config=config,
    )
    assert encrypted["tokens"]["access_token"] == {
        "$doppler": {"name": "PFX_TOKENS_ACCESS_TOKEN"}
    }
    assert encrypted["tokens"]["refresh_token"] == {
        "$doppler": {"name": "PFX_TOKENS_REFRESH_TOKEN"}
    }


@respx.mock
def test_decrypt_bulk_reads_once_serving_multiple_fields():
    """Three sensitive fields = ONE HTTPS round-trip."""
    config = {
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c",
        "name_prefix": "PFX",
    }
    route = respx.get("https://api.doppler.com/v3/configs/config/secrets").mock(
        return_value=httpx.Response(200, json={"secrets": {
            "PFX_CLIENT_SECRET": {"raw": "csec", "computed": "csec"},
            "PFX_TOKENS_ACCESS_TOKEN": {"raw": "AT", "computed": "AT"},
            "PFX_TOKENS_REFRESH_TOKEN": {"raw": "RT", "computed": "RT"},
        }}),
    )
    b = DopplerBackend()
    data = {
        "client_secret": {"$doppler": {"name": "PFX_CLIENT_SECRET"}},
        "tokens": {
            "access_token": {"$doppler": {"name": "PFX_TOKENS_ACCESS_TOKEN"}},
            "refresh_token": {"$doppler": {"name": "PFX_TOKENS_REFRESH_TOKEN"}},
        },
    }
    decrypted = b.decrypt_fields(
        data,
        sensitive_field_paths=(
            "client_secret", "tokens.access_token", "tokens.refresh_token",
        ),
        backend_config=config,
    )
    assert decrypted["client_secret"] == "csec"
    assert decrypted["tokens"]["access_token"] == "AT"
    assert decrypted["tokens"]["refresh_token"] == "RT"
    # Bulk: exactly ONE GET
    assert route.call_count == 1


@respx.mock
def test_decrypt_uses_computed_value():
    """Doppler returns both raw and computed; we use computed (resolves refs)."""
    config = {
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c", "name_prefix": "PFX",
    }
    respx.get("https://api.doppler.com/v3/configs/config/secrets").mock(
        return_value=httpx.Response(200, json={"secrets": {
            "PFX_SECRET": {
                "raw": "{{secrets.OTHER}}",
                "computed": "resolved-value",
            },
        }}),
    )
    b = DopplerBackend()
    data = {"x": {"$doppler": {"name": "PFX_SECRET"}}}
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("x",), backend_config=config,
    )
    assert decrypted["x"] == "resolved-value"


@respx.mock
def test_decrypt_missing_name_raises_DopplerApiError():
    config = {
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c", "name_prefix": "PFX",
    }
    respx.get("https://api.doppler.com/v3/configs/config/secrets").mock(
        return_value=httpx.Response(200, json={"secrets": {}}),  # empty
    )
    b = DopplerBackend()
    data = {"x": {"$doppler": {"name": "MISSING_NAME"}}}
    with pytest.raises(DopplerApiError, match="MISSING_NAME"):
        b.decrypt_fields(
            data, sensitive_field_paths=("x",), backend_config=config,
        )


@respx.mock
def test_decrypt_401_raises_DopplerAuthError():
    config = {
        "service_token": "dp.st.dev.bad",
        "project": "p", "config": "c", "name_prefix": "PFX",
    }
    respx.get("https://api.doppler.com/v3/configs/config/secrets").mock(
        return_value=httpx.Response(401, json={"messages": ["invalid"], "success": False}),
    )
    b = DopplerBackend()
    data = {"x": {"$doppler": {"name": "PFX_X"}}}
    with pytest.raises(DopplerAuthError):
        b.decrypt_fields(
            data, sensitive_field_paths=("x",), backend_config=config,
        )


@respx.mock
def test_health_ok_when_config_reachable():
    respx.get("https://api.doppler.com/v3/configs/config").mock(
        return_value=httpx.Response(200, json={"config": {"name": "prd"}}),
    )
    b = DopplerBackend()
    result = b.health({
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c", "name_prefix": "PFX",
    })
    assert result.ok is True


@respx.mock
def test_health_not_ok_on_401():
    respx.get("https://api.doppler.com/v3/configs/config").mock(
        return_value=httpx.Response(401, json={"messages": ["unauthorized"]}),
    )
    b = DopplerBackend()
    result = b.health({
        "service_token": "dp.st.dev.bad",
        "project": "p", "config": "c", "name_prefix": "PFX",
    })
    assert result.ok is False
    assert "invalid" in (result.message or "").lower() or "401" in (result.message or "")


@respx.mock
def test_health_not_ok_on_404():
    respx.get("https://api.doppler.com/v3/configs/config").mock(
        return_value=httpx.Response(404, json={"messages": ["not found"]}),
    )
    b = DopplerBackend()
    result = b.health({
        "service_token": "dp.st.dev.x",
        "project": "wrong-project", "config": "c", "name_prefix": "PFX",
    })
    assert result.ok is False


def test_decrypt_no_markers_skips_http_call():
    """If protocol_data has no $doppler markers, we don't hit Doppler."""
    b = DopplerBackend()
    data = {"client_secret": "plain-text", "client_id": "cid"}
    config = {
        "service_token": "dp.st.dev.x",
        "project": "p", "config": "c", "name_prefix": "PFX",
    }
    # No respx mock — would error if any HTTP call were made
    decrypted = b.decrypt_fields(
        data,
        sensitive_field_paths=("client_secret",),
        backend_config=config,
    )
    assert decrypted == data  # unchanged
