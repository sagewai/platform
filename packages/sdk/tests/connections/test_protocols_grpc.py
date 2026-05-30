# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""gRPC plugin schema + error tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.protocols.grpc import (
    GrpcAuthError,
    GrpcCallError,
    GrpcConnectionError,
    GrpcDeadlineError,
    GrpcError,
    GrpcMarshalError,
    GrpcMethodError,
    GrpcNotInstalledError,
    GrpcProtocolData,
    GrpcProtocolPlugin,
)


def test_error_hierarchy():
    for cls in (
        GrpcNotInstalledError,
        GrpcConnectionError,
        GrpcAuthError,
        GrpcMethodError,
        GrpcMarshalError,
        GrpcDeadlineError,
        GrpcCallError,
    ):
        assert issubclass(cls, GrpcError)


def test_error_codes_stable():
    assert GrpcError.code == "grpc_error"
    assert GrpcNotInstalledError.code == "grpc_not_installed"
    assert GrpcConnectionError.code == "grpc_connection_error"
    assert GrpcAuthError.code == "grpc_auth_error"
    assert GrpcMethodError.code == "grpc_method_error"
    assert GrpcMarshalError.code == "grpc_marshal_error"
    assert GrpcDeadlineError.code == "grpc_deadline_exceeded"
    assert GrpcCallError.code == "grpc_call_error"


def test_call_error_carries_status_and_details():
    err = GrpcCallError(status_code="RESOURCE_EXHAUSTED", details="quota exceeded")
    assert err.status_code == "RESOURCE_EXHAUSTED"
    assert err.details == "quota exceeded"
    assert "RESOURCE_EXHAUSTED" in str(err)


# ── schema ────────────────────────────────────────────────────────────


def test_protocol_data_minimal():
    d = GrpcProtocolData(target="api.example.com:443")
    assert d.target == "api.example.com:443"
    assert d.tls == "tls"
    assert d.auth_mode == "none"
    assert d.default_timeout_seconds == 30.0


def test_protocol_data_rejects_extra():
    with pytest.raises(ValidationError):
        GrpcProtocolData(target="x:1", bogus=True)


def test_tls_ca_requires_cert():
    with pytest.raises(ValidationError, match="tls_ca_cert"):
        GrpcProtocolData(target="x:1", tls="tls_ca")
    ok = GrpcProtocolData(target="x:1", tls="tls_ca", tls_ca_cert="-----BEGIN CERT-----...")
    assert ok.tls == "tls_ca"


def test_metadata_token_requires_key():
    with pytest.raises(ValidationError):
        GrpcProtocolData(target="x:1", auth_mode="metadata_token", auth_metadata_key="")
    ok = GrpcProtocolData(target="x:1", auth_mode="metadata_token", auth_token="t")
    assert ok.auth_metadata_key == "authorization"


def test_timeout_positive():
    with pytest.raises(ValidationError):
        GrpcProtocolData(target="x:1", default_timeout_seconds=0)


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = GrpcProtocolPlugin()
    assert p.id == "grpc"
    assert p.display_name == "gRPC"
    assert p.sensitive_fields == ("auth_token",)


def test_plugin_is_protocol_plugin_only():
    from sagewai.connections.protocols.base import ProtocolPlugin
    from sagewai.connections.subscriptions.base import SubscriptionPlugin

    p = GrpcProtocolPlugin()
    assert isinstance(p, ProtocolPlugin)
    assert not isinstance(p, SubscriptionPlugin)  # unary only — no streaming


def test_public_view_masks_token():
    p = GrpcProtocolPlugin()
    out = p.public_view({"target": "x:1", "auth_token": "secret"})
    assert out["auth_token"] == "***"
    assert (
        p.public_view({"target": "x:1", "auth_token": "secret"}, include_secrets=True)[
            "auth_token"
        ]
        == "secret"
    )
