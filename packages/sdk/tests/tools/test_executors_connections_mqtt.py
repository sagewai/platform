# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog executor — MQTT stateful dispatch to the SubscriptionManager.

MQTT is the first STATEFUL connection kind: the executor routes
subscribe/drain/unsubscribe to the process-wide SubscriptionManager via
``get_subscription_manager()`` rather than the stateless ``_run_op``
decrypt-then-call path the four Phase A kinds use.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.store import ConnectionStore
from sagewai.connections.subscriptions.base import DrainResult
from sagewai.tools.executors.connections import run as connections_run


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        store_path=tmp_path / "connections.json",
        allowed_protocols=("mqtt",),
    )


def _seed(store):
    return store.create(
        protocol="mqtt", project_id="proj", display_name="broker", tags=["fleet"],
        credentials_backend={"kind": "local"},
        protocol_data={"host": "broker.example.com", "port": 1883},
    )


@pytest.mark.asyncio
async def test_subscribe_op_dispatches_to_manager(store):
    _seed(store)
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(return_value="sub-xyz")
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "broker", "operation": "subscribe",
                          "args": {"topic_filter": "fleet/+/loc", "qos": 1}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result == {"subscription_id": "sub-xyz"}
    kwargs = fake_mgr.subscribe.await_args.kwargs
    assert kwargs["spec"] == {"topic_filter": "fleet/+/loc", "qos": 1}
    # plugin must be the MQTT plugin; connection resolved from the store.
    from sagewai.connections.protocols.mqtt import MqttProtocolPlugin

    assert isinstance(kwargs["plugin"], MqttProtocolPlugin)
    assert kwargs["connection"].display_name == "broker"


@pytest.mark.asyncio
async def test_drain_op_returns_drain_result(store):
    _seed(store)
    dr = DrainResult(events=[{"topic": "t", "payload": "p"}], returned=1, remaining=0,
                     overflow_dropped=0, oversized_dropped=0, global_pressure_dropped=0)
    fake_mgr = MagicMock()
    fake_mgr.drain = AsyncMock(return_value=dr)
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "broker", "operation": "drain",
                          "args": {"subscription_id": "sub-xyz", "max_events": 50}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result["returned"] == 1
    assert result["events"] == [{"topic": "t", "payload": "p"}]
    fake_mgr.drain.assert_awaited_with("sub-xyz", 50)


@pytest.mark.asyncio
async def test_drain_op_default_max_events(store):
    _seed(store)
    dr = DrainResult(events=[], returned=0, remaining=0,
                     overflow_dropped=0, oversized_dropped=0, global_pressure_dropped=0)
    fake_mgr = MagicMock()
    fake_mgr.drain = AsyncMock(return_value=dr)
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "broker", "operation": "drain",
                          "args": {"subscription_id": "sub-xyz"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        await connections_run(payload, store=store)
    fake_mgr.drain.assert_awaited_with("sub-xyz", 100)


@pytest.mark.asyncio
async def test_unsubscribe_op(store):
    _seed(store)
    fake_mgr = MagicMock()
    fake_mgr.unsubscribe = AsyncMock(return_value=None)
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "broker", "operation": "unsubscribe",
                          "args": {"subscription_id": "sub-xyz"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result == {"ok": True}
    fake_mgr.unsubscribe.assert_awaited_with("sub-xyz")


@pytest.mark.asyncio
async def test_unknown_op_raises(store):
    _seed(store)
    fake_mgr = MagicMock()
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "broker", "operation": "publish",
                          "args": {}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        with pytest.raises(ValueError, match="unknown mqtt operation"):
            await connections_run(payload, store=store)


@pytest.mark.asyncio
async def test_subscribe_unknown_connection_raises(store):
    # No seed → connection_ref doesn't resolve.
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(return_value="sub")
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "nope", "operation": "subscribe",
                          "args": {"topic_filter": "t"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=fake_mgr,
    ):
        with pytest.raises(ValueError, match="not found"):
            await connections_run(payload, store=store)


def test_mqtt_in_executor_registry():
    from sagewai.tools.executors import get
    from sagewai.tools.executors.connections import run

    assert get("mqtt") is run
