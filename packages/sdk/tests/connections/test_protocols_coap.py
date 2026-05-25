# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CoAP protocol plugin tests (schema + errors + public_view)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.protocols.coap import (
    CoapConnectionError,
    CoapDtlsError,
    CoapError,
    CoapNotInstalledError,
    CoapProtocolData,
    CoapProtocolError,
    CoapProtocolPlugin,
    CoapTimeoutError,
)


# ── errors ────────────────────────────────────────────────────────────


def test_error_hierarchy():
    assert issubclass(CoapNotInstalledError, CoapError)
    assert issubclass(CoapTimeoutError, CoapError)
    assert issubclass(CoapProtocolError, CoapError)
    assert issubclass(CoapDtlsError, CoapError)
    assert issubclass(CoapConnectionError, CoapError)


def test_error_codes_stable():
    assert CoapError.code == "coap_error"
    assert CoapNotInstalledError.code == "coap_not_installed"
    assert CoapTimeoutError.code == "coap_timeout"
    assert CoapProtocolError.code == "coap_protocol_error"
    assert CoapDtlsError.code == "coap_dtls_error"
    assert CoapConnectionError.code == "coap_connection_error"


def test_protocol_error_carries_code_and_payload():
    err = CoapProtocolError(coap_code="4.04", payload=b"Not Found")
    assert err.coap_code == "4.04"
    assert err.payload == b"Not Found"
    assert "4.04" in str(err)


# ── schema ────────────────────────────────────────────────────────────


def test_schema_minimal_valid():
    data = CoapProtocolData(base_uri="coap://device.example.com:5683")
    assert data.base_uri == "coap://device.example.com:5683"
    assert data.use_dtls is False
    assert data.psk_identity == ""
    assert data.psk_key == ""
    assert data.default_timeout_seconds == 10.0
    assert data.sandbox_tier_override is None


def test_schema_coaps_scheme_valid():
    data = CoapProtocolData(
        base_uri="coaps://device.example.com:5684",
        psk_identity="client1",
        psk_key="hex-key",
    )
    assert data.base_uri.startswith("coaps://")


def test_schema_rejects_non_coap_scheme():
    with pytest.raises(ValidationError):
        CoapProtocolData(base_uri="https://example.com")


def test_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CoapProtocolData(
            base_uri="coap://device.example.com",
            unknown_field=True,
        )


def test_schema_sandbox_tier_override_accepts_downgrade_values_only():
    # Per spec: only TRUSTED or SANDBOXED (CoAP default is SANDBOXED;
    # only TRUSTED counts as a downgrade, but the schema permits both
    # so operators can re-set the default explicitly).
    ok = CoapProtocolData(
        base_uri="coap://x.com",
        sandbox_tier_override="TRUSTED",
    )
    assert ok.sandbox_tier_override == "TRUSTED"
    with pytest.raises(ValidationError):
        CoapProtocolData(
            base_uri="coap://x.com",
            sandbox_tier_override="UNTRUSTED",
        )


def test_schema_default_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        CoapProtocolData(
            base_uri="coap://x.com",
            default_timeout_seconds=0,
        )


def test_schema_use_dtls_auto_aligns_with_scheme():
    """``use_dtls`` is redundant with the scheme — the model enforces
    consistency. coap:// + use_dtls=False is valid; coaps:// + use_dtls=True
    is valid; the mismatched combinations are rejected."""
    # Plaintext + use_dtls=False — OK.
    ok = CoapProtocolData(base_uri="coap://x.com", use_dtls=False)
    assert ok.use_dtls is False
    # DTLS scheme + use_dtls=True — OK.
    ok2 = CoapProtocolData(base_uri="coaps://x.com", use_dtls=True)
    assert ok2.use_dtls is True


def test_schema_rejects_coaps_with_use_dtls_false():
    """coaps:// scheme MUST have use_dtls=True (or unset, which auto-derives)."""
    with pytest.raises(ValidationError):
        CoapProtocolData(base_uri="coaps://x.com", use_dtls=False)


def test_schema_rejects_coap_with_use_dtls_true():
    """coap:// (plaintext) MUST NOT claim DTLS."""
    with pytest.raises(ValidationError):
        CoapProtocolData(base_uri="coap://x.com", use_dtls=True)


def test_schema_use_dtls_default_matches_scheme():
    """When ``use_dtls`` isn't provided, it defaults to match the scheme."""
    plaintext = CoapProtocolData(base_uri="coap://x.com")
    assert plaintext.use_dtls is False
    secure = CoapProtocolData(base_uri="coaps://x.com")
    assert secure.use_dtls is True


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = CoapProtocolPlugin()
    assert p.id == "coap"
    assert p.display_name == "CoAP"
    assert p.sensitive_fields == ("psk_key",)


def test_plugin_schema_returns_pydantic_model():
    p = CoapProtocolPlugin()
    assert p.protocol_data_schema() is CoapProtocolData


# ── public_view ───────────────────────────────────────────────────────


def test_public_view_masks_psk_key_by_default():
    p = CoapProtocolPlugin()
    data = {
        "base_uri": "coaps://device.example.com",
        "psk_identity": "client1",
        "psk_key": "abcdef0123456789",
    }
    out = p.public_view(data)
    assert out["base_uri"] == "coaps://device.example.com"
    assert out["psk_identity"] == "client1"
    assert out["psk_key"] == "***"


def test_public_view_includes_secrets_when_requested():
    p = CoapProtocolPlugin()
    data = {
        "base_uri": "coaps://device.example.com",
        "psk_identity": "client1",
        "psk_key": "abcdef0123456789",
    }
    out = p.public_view(data, include_secrets=True)
    assert out["psk_key"] == "abcdef0123456789"


def test_public_view_missing_psk_key_unchanged():
    p = CoapProtocolPlugin()
    data = {"base_uri": "coap://device.example.com"}
    out = p.public_view(data)
    assert out == {"base_uri": "coap://device.example.com"}


def test_plugin_registered_in_PROTOCOLS():
    from sagewai.connections.protocols import PROTOCOLS, get_protocol

    ids = {p.id for p in PROTOCOLS}
    assert "coap" in ids
    plugin = get_protocol("coap")
    assert isinstance(plugin, CoapProtocolPlugin)


def test_plugin_runtime_checkable_protocol():
    from sagewai.connections.protocols.base import ProtocolPlugin

    plugin = CoapProtocolPlugin()
    assert isinstance(plugin, ProtocolPlugin)
