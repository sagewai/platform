# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MQTT plugin schema + errors + ProtocolPlugin-half tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.mqtt import (
    MqttAuthError,
    MqttConnectionError,
    MqttError,
    MqttNotInstalledError,
    MqttProtocolData,
    MqttProtocolPlugin,
    MqttSubscribeError,
    MqttSubscriptionSpec,
    MqttTlsError,
)


# ── errors ────────────────────────────────────────────────────────────


def test_error_hierarchy():
    for cls in (
        MqttNotInstalledError,
        MqttConnectionError,
        MqttAuthError,
        MqttSubscribeError,
        MqttTlsError,
    ):
        assert issubclass(cls, MqttError)


def test_error_codes_stable():
    assert MqttError.code == "mqtt_error"
    assert MqttNotInstalledError.code == "mqtt_not_installed"
    assert MqttConnectionError.code == "mqtt_connection_error"
    assert MqttAuthError.code == "mqtt_auth_error"
    assert MqttSubscribeError.code == "mqtt_subscribe_error"
    assert MqttTlsError.code == "mqtt_tls_error"


# ── connection schema ─────────────────────────────────────────────────


def test_protocol_data_minimal():
    d = MqttProtocolData(host="broker.example.com")
    assert d.host == "broker.example.com"
    assert d.port == 1883
    assert d.transport == "tcp"
    assert d.mqtt_version == "5.0"
    assert d.keepalive_seconds == 60
    assert d.password == ""


def test_protocol_data_rejects_extra():
    with pytest.raises(ValidationError):
        MqttProtocolData(host="x", bogus=True)


def test_protocol_data_rejects_bad_transport():
    with pytest.raises(ValidationError):
        MqttProtocolData(host="x", transport="quic")


def test_protocol_data_keepalive_positive():
    with pytest.raises(ValidationError):
        MqttProtocolData(host="x", keepalive_seconds=0)


def test_protocol_data_rejects_bad_port():
    with pytest.raises(ValidationError):
        MqttProtocolData(host="x", port=70000)


# ── subscription spec ─────────────────────────────────────────────────


def test_subscription_spec_minimal():
    s = MqttSubscriptionSpec(topic_filter="sensors/+/temp")
    assert s.topic_filter == "sensors/+/temp"
    assert s.qos == 0
    assert s.overflow_policy == "drop_oldest"


def test_subscription_spec_rejects_pause_in_pr2():
    """PR2 ships drop_oldest only; pause is a deferred follow-up."""
    with pytest.raises(ValidationError):
        MqttSubscriptionSpec(topic_filter="x", overflow_policy="pause")


def test_subscription_spec_qos_levels():
    for q in (0, 1, 2):
        assert MqttSubscriptionSpec(topic_filter="x", qos=q).qos == q
    with pytest.raises(ValidationError):
        MqttSubscriptionSpec(topic_filter="x", qos=3)


def test_subscription_spec_rejects_empty_topic():
    with pytest.raises(ValidationError):
        MqttSubscriptionSpec(topic_filter="")


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = MqttProtocolPlugin()
    assert p.id == "mqtt"
    assert p.display_name == "MQTT"
    assert p.sensitive_fields == ("password",)


def test_plugin_implements_both_protocols():
    from sagewai.connections.protocols.base import ProtocolPlugin
    from sagewai.connections.subscriptions.base import SubscriptionPlugin

    p = MqttProtocolPlugin()
    assert isinstance(p, ProtocolPlugin)
    assert isinstance(p, SubscriptionPlugin)


def test_subscription_spec_schema_returns_model():
    p = MqttProtocolPlugin()
    assert p.subscription_spec_schema() is MqttSubscriptionSpec


def test_public_view_masks_password():
    p = MqttProtocolPlugin()
    out = p.public_view({"host": "x", "password": "secret"})
    assert out["password"] == "***"
    out2 = p.public_view({"host": "x", "password": "secret"}, include_secrets=True)
    assert out2["password"] == "secret"


def test_public_view_no_password_no_mask():
    p = MqttProtocolPlugin()
    out = p.public_view({"host": "x"})
    assert "password" not in out or out.get("password") in ("", None)


# ── plugin registration ───────────────────────────────────────────────


def test_plugin_registered_in_protocols():
    from sagewai.connections.protocols import PROTOCOLS, get_protocol

    assert any(p.id == "mqtt" for p in PROTOCOLS)
    assert isinstance(get_protocol("mqtt"), MqttProtocolPlugin)


# ── test() ────────────────────────────────────────────────────────────


def _conn(pd: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="c", protocol="mqtt", project_id="p", display_name="b", tags=(),
        credentials_backend={"kind": "local"}, status="ready", last_tested_at=None,
        last_test_ok=None, is_default=False, created_at=now, updated_at=now,
        last_error=None, protocol_data=pd,
    )


@pytest.mark.asyncio
async def test_test_connects_and_disconnects():
    """plugin.test() connects + immediately disconnects (no subscribe)."""
    conn = _conn({"host": "broker", "port": 1883})

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.subscribe = AsyncMock()

    with patch("aiomqtt.Client", return_value=fake_client):
        plugin = MqttProtocolPlugin()
        result = await plugin.test(conn, ctx=MagicMock())
    assert result.ok is True
    # subscribe must NOT have been called
    assert not fake_client.subscribe.called


@pytest.mark.asyncio
async def test_test_connect_failure_returns_not_ok():
    conn = _conn({"host": "unreachable"})
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(side_effect=OSError("connection refused"))
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("aiomqtt.Client", return_value=fake_client):
        plugin = MqttProtocolPlugin()
        result = await plugin.test(conn, ctx=MagicMock())
    assert result.ok is False
    assert "refused" in (result.message or "").lower()
