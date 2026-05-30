# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WebSocket protocol plugin tests (schemas + errors + public_view)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.protocols.websocket import (
    WebsocketAuthError,
    WebsocketConnectionError,
    WebsocketError,
    WebsocketHandshakeError,
    WebsocketNotInstalledError,
    WebsocketOperation,
    WebsocketProtocolData,
    WebsocketProtocolPlugin,
    WebsocketResponseError,
    WebsocketTemplateError,
    WebsocketTimeoutError,
    WebsocketUnknownOperationError,
)


# ── errors ────────────────────────────────────────────────────────────


def test_error_hierarchy():
    assert issubclass(WebsocketNotInstalledError, WebsocketError)
    assert issubclass(WebsocketConnectionError, WebsocketError)
    assert issubclass(WebsocketHandshakeError, WebsocketError)
    assert issubclass(WebsocketAuthError, WebsocketError)
    assert issubclass(WebsocketTimeoutError, WebsocketError)
    assert issubclass(WebsocketTemplateError, WebsocketError)
    assert issubclass(WebsocketResponseError, WebsocketError)
    assert issubclass(WebsocketUnknownOperationError, WebsocketError)


def test_error_codes_stable():
    assert WebsocketError.code == "websocket_error"
    assert WebsocketNotInstalledError.code == "websocket_not_installed"
    assert WebsocketConnectionError.code == "websocket_connection_error"
    assert WebsocketHandshakeError.code == "websocket_handshake_error"
    assert WebsocketAuthError.code == "websocket_auth_error"
    assert WebsocketTimeoutError.code == "websocket_timeout"
    assert WebsocketTemplateError.code == "websocket_template_error"
    assert WebsocketResponseError.code == "websocket_response_error"
    assert WebsocketUnknownOperationError.code == "websocket_unknown_operation"


def test_template_error_lists_missing_keys():
    err = WebsocketTemplateError(missing_keys=["device_id", "topic"])
    assert err.missing_keys == ["device_id", "topic"]
    assert "device_id" in str(err)
    assert "topic" in str(err)


def test_response_error_carries_frame():
    err = WebsocketResponseError("JSONPath did not match", frame='{"foo": 1}')
    assert err.frame == '{"foo": 1}'
    assert "JSONPath did not match" in str(err)


def test_unknown_operation_carries_name():
    err = WebsocketUnknownOperationError(name="ping_xyz")
    assert err.name == "ping_xyz"
    assert "ping_xyz" in str(err)


# ── operation schema ──────────────────────────────────────────────────


def test_operation_schema_valid_minimal():
    op = WebsocketOperation(
        name="get_status",
        message_template='{"action": "status"}',
    )
    assert op.name == "get_status"
    assert op.message_template == '{"action": "status"}'
    assert op.response_match is None
    assert op.timeout_seconds is None


def test_operation_schema_with_response_match():
    op = WebsocketOperation(
        name="get_quote",
        message_template='{"symbol": "{symbol}"}',
        response_match="$.price",
    )
    assert op.response_match == "$.price"


def test_operation_schema_with_per_op_timeout():
    op = WebsocketOperation(
        name="slow_op",
        message_template='{"x": 1}',
        timeout_seconds=60.0,
    )
    assert op.timeout_seconds == 60.0


def test_operation_schema_rejects_empty_name():
    with pytest.raises(ValidationError):
        WebsocketOperation(name="", message_template='{"x": 1}')


def test_operation_schema_rejects_empty_template():
    with pytest.raises(ValidationError):
        WebsocketOperation(name="x", message_template="")


def test_operation_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        WebsocketOperation(name="x", message_template='{}', unknown_field=True)


def test_operation_schema_rejects_negative_timeout():
    with pytest.raises(ValidationError):
        WebsocketOperation(name="x", message_template='{}', timeout_seconds=-1)


# ── protocol_data schema ──────────────────────────────────────────────


def test_protocol_data_schema_minimal_valid():
    data = WebsocketProtocolData(url="wss://gateway.example.com/ws")
    assert data.url == "wss://gateway.example.com/ws"
    assert data.headers == {}
    assert data.auth_header_name == "Authorization"
    assert data.auth_header_value == ""
    assert data.default_timeout_seconds == 30.0
    assert data.operations == []
    assert data.sandbox_tier_override is None


def test_protocol_data_schema_ws_scheme_accepted():
    data = WebsocketProtocolData(url="ws://localhost:8080/ws")
    assert data.url == "ws://localhost:8080/ws"


def test_protocol_data_schema_rejects_non_ws_url():
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="http://x.com/ws")
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="https://x.com/ws")


def test_protocol_data_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="wss://x.com", unknown_field=True)


def test_protocol_data_schema_default_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="wss://x.com", default_timeout_seconds=0)
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="wss://x.com", default_timeout_seconds=-1)


def test_protocol_data_schema_operations_unique_names():
    """Two operations with the same name should be rejected at schema time."""
    with pytest.raises(ValidationError, match="duplicate operation name"):
        WebsocketProtocolData(
            url="wss://x.com",
            operations=[
                {"name": "op1", "message_template": '{"a": 1}'},
                {"name": "op1", "message_template": '{"b": 2}'},
            ],
        )


def test_protocol_data_schema_custom_auth_header_name():
    data = WebsocketProtocolData(
        url="wss://x.com",
        auth_header_name="X-API-Key",
        auth_header_value="secret123",
    )
    assert data.auth_header_name == "X-API-Key"
    assert data.auth_header_value == "secret123"


def test_protocol_data_schema_sandbox_tier_override():
    ok = WebsocketProtocolData(url="wss://x.com", sandbox_tier_override="TRUSTED")
    assert ok.sandbox_tier_override == "TRUSTED"
    with pytest.raises(ValidationError):
        WebsocketProtocolData(url="wss://x.com", sandbox_tier_override="bogus")


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = WebsocketProtocolPlugin()
    assert p.id == "websocket"
    assert p.display_name == "WebSocket"
    assert p.sensitive_fields == ("auth_header_value",)


def test_plugin_schema_returns_pydantic_model():
    p = WebsocketProtocolPlugin()
    assert p.protocol_data_schema() is WebsocketProtocolData


# ── public_view ───────────────────────────────────────────────────────


def test_public_view_masks_auth_header_value_by_default():
    p = WebsocketProtocolPlugin()
    data = {
        "url": "wss://x.com",
        "auth_header_name": "Authorization",
        "auth_header_value": "Bearer abc123",
        "headers": {},
        "operations": [],
    }
    out = p.public_view(data)
    assert out["auth_header_name"] == "Authorization"
    assert out["auth_header_value"] == "***"


def test_public_view_includes_secrets_when_requested():
    p = WebsocketProtocolPlugin()
    data = {"url": "wss://x.com", "auth_header_value": "Bearer abc123"}
    out = p.public_view(data, include_secrets=True)
    assert out["auth_header_value"] == "Bearer abc123"


def test_public_view_empty_auth_header_value_unchanged():
    p = WebsocketProtocolPlugin()
    data = {"url": "wss://x.com", "auth_header_value": ""}
    out = p.public_view(data)
    assert out["auth_header_value"] == ""


# ── registration ──────────────────────────────────────────────────────


def test_plugin_registered_in_PROTOCOLS():
    from sagewai.connections.protocols import PROTOCOLS, get_protocol

    ids = {p.id for p in PROTOCOLS}
    assert "websocket" in ids
    plugin = get_protocol("websocket")
    assert isinstance(plugin, WebsocketProtocolPlugin)


def test_plugin_runtime_checkable_protocol():
    from sagewai.connections.protocols.base import ProtocolPlugin

    plugin = WebsocketProtocolPlugin()
    assert isinstance(plugin, ProtocolPlugin)


def test_protocols_count_is_10_after_mqtt():
    """Sentinel test — Phase A (9) + MQTT (Phase B subscription) = 10."""
    from sagewai.connections.protocols import PROTOCOLS

    assert len(PROTOCOLS) == 10
