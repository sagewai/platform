# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OPC UA protocol plugin tests (schemas + errors + public_view)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.protocols.opcua import (
    OpcuaAuthError,
    OpcuaConnectionError,
    OpcuaError,
    OpcuaNotInstalledError,
    OpcuaOperation,
    OpcuaProtocolData,
    OpcuaProtocolPlugin,
    OpcuaReadError,
    OpcuaSessionError,
    OpcuaUnknownOperationError,
)


# ── errors ────────────────────────────────────────────────────────────


def test_error_hierarchy():
    assert issubclass(OpcuaNotInstalledError, OpcuaError)
    assert issubclass(OpcuaConnectionError, OpcuaError)
    assert issubclass(OpcuaAuthError, OpcuaError)
    assert issubclass(OpcuaSessionError, OpcuaError)
    assert issubclass(OpcuaReadError, OpcuaError)
    assert issubclass(OpcuaUnknownOperationError, OpcuaError)


def test_error_codes_stable():
    assert OpcuaError.code == "opcua_error"
    assert OpcuaNotInstalledError.code == "opcua_not_installed"
    assert OpcuaConnectionError.code == "opcua_connection_error"
    assert OpcuaAuthError.code == "opcua_auth_error"
    assert OpcuaSessionError.code == "opcua_session_error"
    assert OpcuaReadError.code == "opcua_read_error"
    assert OpcuaUnknownOperationError.code == "opcua_unknown_operation"


def test_read_error_carries_node_id_and_status():
    err = OpcuaReadError(node_id="ns=2;s=Devices/Sensor1/Temperature", status_code="BadNodeIdUnknown")
    assert err.node_id == "ns=2;s=Devices/Sensor1/Temperature"
    assert err.status_code == "BadNodeIdUnknown"
    assert "ns=2;s=Devices/Sensor1/Temperature" in str(err)


def test_unknown_operation_carries_name():
    err = OpcuaUnknownOperationError(name="read_xyz")
    assert err.name == "read_xyz"
    assert "read_xyz" in str(err)


# ── operation schema ──────────────────────────────────────────────────


def test_operation_schema_valid():
    op = OpcuaOperation(
        name="read_temperature",
        kind="read",
        node_id="ns=2;s=Devices/Sensor1/Temperature",
    )
    assert op.name == "read_temperature"
    assert op.kind == "read"
    assert op.node_id == "ns=2;s=Devices/Sensor1/Temperature"


def test_operation_schema_rejects_invalid_kind():
    """Phase A only supports kind=read. method_call and subscribe are deferred."""
    with pytest.raises(ValidationError):
        OpcuaOperation(name="x", kind="method_call", node_id="ns=2;i=42")
    with pytest.raises(ValidationError):
        OpcuaOperation(name="x", kind="subscribe", node_id="ns=2;i=42")


def test_operation_schema_rejects_invalid_node_id():
    """NodeId grammar: ns=<n>;[isgb]=<value> per IEC 62541."""
    with pytest.raises(ValidationError):
        OpcuaOperation(name="x", kind="read", node_id="invalid")
    with pytest.raises(ValidationError):
        OpcuaOperation(name="x", kind="read", node_id="not a node id")


def test_operation_schema_accepts_namespace_optional():
    """NodeId without explicit namespace defaults to ns=0."""
    op = OpcuaOperation(name="x", kind="read", node_id="i=2259")
    assert op.node_id == "i=2259"


def test_operation_schema_accepts_all_identifier_types():
    """OPC UA NodeId identifier types: i (numeric), s (string), g (guid), b (bytestring)."""
    for ident in ["ns=2;i=42", "ns=2;s=Foo", "ns=2;g=09087e75-8e5e-499b-954f-f2a9603db28a", "ns=2;b=YWJj"]:
        op = OpcuaOperation(name=f"op_{ident}", kind="read", node_id=ident)
        assert op.node_id == ident


def test_operation_schema_rejects_empty_name():
    with pytest.raises(ValidationError):
        OpcuaOperation(name="", kind="read", node_id="ns=2;i=42")


# ── protocol_data schema ──────────────────────────────────────────────


def test_protocol_data_schema_minimal_valid():
    data = OpcuaProtocolData(endpoint_url="opc.tcp://server.example.com:4840")
    assert data.endpoint_url == "opc.tcp://server.example.com:4840"
    assert data.security_mode == "None"
    assert data.security_policy == "None"
    assert data.auth_mode == "anonymous"
    assert data.username == ""
    assert data.password == ""
    assert data.operations == []
    assert data.sandbox_tier_override is None


def test_protocol_data_schema_username_auth_requires_username():
    """When auth_mode=username, username must be non-empty."""
    with pytest.raises(ValidationError, match="username"):
        OpcuaProtocolData(
            endpoint_url="opc.tcp://x:4840",
            auth_mode="username",
            username="",
        )


def test_protocol_data_schema_username_auth_accepts_blank_password_field():
    """Password may be empty string at schema time — it's filled by credentials marker.

    The validator must not require non-empty password (which would block
    encrypted marker forms like {'$env': 'X'}).
    """
    data = OpcuaProtocolData(
        endpoint_url="opc.tcp://x:4840",
        auth_mode="username",
        username="svc_account",
        password="",  # empty allowed at schema-time
    )
    assert data.username == "svc_account"


def test_protocol_data_schema_anonymous_ignores_username_password():
    data = OpcuaProtocolData(
        endpoint_url="opc.tcp://x:4840",
        auth_mode="anonymous",
        # username/password fields ignored when anonymous
    )
    assert data.auth_mode == "anonymous"


def test_protocol_data_schema_rejects_non_opc_tcp_url():
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="http://x:4840")
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="opc.https://x:4840")


def test_protocol_data_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", unknown_field=True)


def test_protocol_data_schema_security_modes_phase_a_accepts_none_only():
    """Phase A only accepts security_mode='None'. Sign / SignAndEncrypt
    require certificate paths and are deferred to Phase B."""
    data = OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_mode="None")
    assert data.security_mode == "None"
    for mode in ["Sign", "SignAndEncrypt"]:
        with pytest.raises(ValidationError, match="Phase B"):
            OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_mode=mode)
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_mode="Bogus")


def test_protocol_data_schema_security_policies_phase_a_accepts_none_only():
    """Phase A only accepts security_policy='None'. Non-default policies
    require certificate paths and are deferred to Phase B."""
    data = OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_policy="None")
    assert data.security_policy == "None"
    for policy in [
        "Basic256Sha256", "Basic256", "Basic128Rsa15",
        "Aes128_Sha256_RsaOaep", "Aes256_Sha256_RsaPss",
    ]:
        with pytest.raises(ValidationError, match="Phase B"):
            OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_policy=policy)
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", security_policy="Custom")


def test_protocol_data_schema_rejects_non_default_security_in_phase_a():
    """Any combination of non-None security_mode or security_policy is
    rejected at schema time during Phase A. Phase B will lift this restriction
    by adding certificate-path fields."""
    # Sign + Basic256Sha256 — common production combo, must be rejected.
    with pytest.raises(ValidationError, match="Phase B"):
        OpcuaProtocolData(
            endpoint_url="opc.tcp://x:4840",
            security_mode="Sign",
            security_policy="Basic256Sha256",
        )
    # SignAndEncrypt alone (with policy=None) — still rejected.
    with pytest.raises(ValidationError, match="Phase B"):
        OpcuaProtocolData(
            endpoint_url="opc.tcp://x:4840",
            security_mode="SignAndEncrypt",
        )
    # policy=Basic256 alone (with mode=None) — still rejected.
    with pytest.raises(ValidationError, match="Phase B"):
        OpcuaProtocolData(
            endpoint_url="opc.tcp://x:4840",
            security_policy="Basic256",
        )
    # The Phase-A baseline (None,None) is accepted.
    ok = OpcuaProtocolData(
        endpoint_url="opc.tcp://x:4840",
        security_mode="None",
        security_policy="None",
    )
    assert ok.security_mode == "None"
    assert ok.security_policy == "None"


def test_protocol_data_schema_operations_list():
    data = OpcuaProtocolData(
        endpoint_url="opc.tcp://x:4840",
        operations=[
            {"name": "read_temp", "kind": "read", "node_id": "ns=2;s=Temp"},
            {"name": "read_pressure", "kind": "read", "node_id": "ns=2;s=Pressure"},
        ],
    )
    assert len(data.operations) == 2
    assert data.operations[0].name == "read_temp"


def test_protocol_data_schema_operations_unique_names():
    """Two operations with the same name should be rejected at schema time."""
    with pytest.raises(ValidationError, match="duplicate operation name"):
        OpcuaProtocolData(
            endpoint_url="opc.tcp://x:4840",
            operations=[
                {"name": "read_temp", "kind": "read", "node_id": "ns=2;s=A"},
                {"name": "read_temp", "kind": "read", "node_id": "ns=2;s=B"},
            ],
        )


def test_protocol_data_schema_sandbox_tier_override():
    ok = OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", sandbox_tier_override="TRUSTED")
    assert ok.sandbox_tier_override == "TRUSTED"
    with pytest.raises(ValidationError):
        OpcuaProtocolData(endpoint_url="opc.tcp://x:4840", sandbox_tier_override="bogus")


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = OpcuaProtocolPlugin()
    assert p.id == "opcua"
    assert p.display_name == "OPC UA"
    assert p.sensitive_fields == ("password",)


def test_plugin_schema_returns_pydantic_model():
    p = OpcuaProtocolPlugin()
    assert p.protocol_data_schema() is OpcuaProtocolData


# ── public_view ───────────────────────────────────────────────────────


def test_public_view_masks_password_by_default():
    p = OpcuaProtocolPlugin()
    data = {
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "username",
        "username": "svc",
        "password": "secret123",
        "operations": [],
    }
    out = p.public_view(data)
    assert out["username"] == "svc"
    assert out["password"] == "***"


def test_public_view_includes_secrets_when_requested():
    p = OpcuaProtocolPlugin()
    data = {
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "username",
        "username": "svc",
        "password": "secret123",
    }
    out = p.public_view(data, include_secrets=True)
    assert out["password"] == "secret123"


def test_public_view_missing_password_unchanged():
    p = OpcuaProtocolPlugin()
    data = {"endpoint_url": "opc.tcp://x:4840", "auth_mode": "anonymous"}
    out = p.public_view(data)
    assert "password" not in out or out.get("password") == ""


# ── registration ──────────────────────────────────────────────────────


def test_plugin_registered_in_PROTOCOLS():
    from sagewai.connections.protocols import PROTOCOLS, get_protocol

    ids = {p.id for p in PROTOCOLS}
    assert "opcua" in ids
    plugin = get_protocol("opcua")
    assert isinstance(plugin, OpcuaProtocolPlugin)


def test_plugin_runtime_checkable_protocol():
    from sagewai.connections.protocols.base import ProtocolPlugin

    plugin = OpcuaProtocolPlugin()
    assert isinstance(plugin, ProtocolPlugin)
