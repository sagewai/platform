# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin v2 MQTT subscription-management routes.

Routes mounted at ``/api/v1/admin/connections/mqtt/``:
- ``POST /{id}/subscribe``            → {subscription_id}
- ``POST /subscriptions/{sub}/drain`` → DrainResult dict
- ``DELETE /subscriptions/{sub}``     → 204
- ``GET /subscriptions``              → list of stats
- ``GET /subscriptions/{sub}``        → stats
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin import connections_v2_routes
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.protocols import mqtt as mqtt_module
from sagewai.connections.protocols import oauth2 as oauth2_module
from sagewai.connections.subscriptions.base import DrainResult, SubscriptionStats
from sagewai.connections.subscriptions.errors import (
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def master_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key)
    return key


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch) -> Path:
    sp = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(sp))
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    return sp


@pytest.fixture
def sf(state_path: Path) -> AdminStateFile:
    sf = AdminStateFile(state_path)
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter22",
    )
    return sf


@pytest.fixture
def token(sf: AdminStateFile) -> str:
    result = sf.validate_login("admin@example.com", "hunter22")
    assert result is not None
    return result["access_token"]


@pytest.fixture
def client(sf: AdminStateFile, token: str, master_key: str) -> TestClient:
    oauth2_module._test_inject_context(None)
    mqtt_module._test_inject_context(None)
    app = FastAPI()
    connections_v2_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    yield tc
    oauth2_module._test_inject_context(None)
    mqtt_module._test_inject_context(None)


def _create_mqtt_connection(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "mqtt",
            "display_name": "fleet-broker",
            "tags": ["fleet"],
            "credentials_backend": {"kind": "local"},
            "protocol_data": {"host": "broker.example.com", "port": 1883},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _stats(sub_id="sub-1", conn_id="c") -> SubscriptionStats:
    return SubscriptionStats(
        subscription_id=sub_id, connection_id=conn_id, status="active",
        buffer_depth=2, bytes_buffered=128, overflow_dropped=0,
        oversized_dropped=0, global_pressure_dropped=0,
        last_event_at=1.0, last_drain_at=0.0, created_at=0.0,
    )


# ── subscribe ────────────────────────────────────────────────────────


def test_subscribe_route(client: TestClient):
    conn_id = _create_mqtt_connection(client)
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(return_value="sub-abc")
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            f"/api/v1/admin/connections/mqtt/{conn_id}/subscribe",
            json={"topic_filter": "fleet/+/loc", "qos": 1},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"subscription_id": "sub-abc"}
    kwargs = fake_mgr.subscribe.await_args.kwargs
    assert kwargs["spec"]["topic_filter"] == "fleet/+/loc"
    assert kwargs["spec"]["qos"] == 1
    assert isinstance(kwargs["plugin"], mqtt_module.MqttProtocolPlugin)


def test_subscribe_route_passes_router_bearing_ctx(client: TestClient):
    """Issue #378: the subscribe route must hand the manager a ctx carrying
    the credentials router (so the subscriber task can decrypt ``password``),
    NOT ``ctx=None``."""
    from sagewai.connections.credentials import CredentialsBackendRouter

    conn_id = _create_mqtt_connection(client)
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(return_value="sub-abc")
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            f"/api/v1/admin/connections/mqtt/{conn_id}/subscribe",
            json={"topic_filter": "fleet/#", "qos": 0},
        )
    assert resp.status_code == 200, resp.text
    ctx = fake_mgr.subscribe.await_args.kwargs["ctx"]
    assert ctx is not None
    assert isinstance(ctx.creds, CredentialsBackendRouter)
    assert ctx.request is None  # long-lived subscription: no pinned Request


def test_subscribe_route_unknown_connection(client: TestClient):
    fake_mgr = MagicMock()
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            "/api/v1/admin/connections/mqtt/nope/subscribe",
            json={"topic_filter": "t"},
        )
    assert resp.status_code == 404


def test_subscribe_route_rejects_pause(client: TestClient):
    conn_id = _create_mqtt_connection(client)
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(return_value="sub-abc")
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            f"/api/v1/admin/connections/mqtt/{conn_id}/subscribe",
            json={"topic_filter": "t", "overflow_policy": "pause"},
        )
    assert resp.status_code == 422


def test_subscribe_route_limit_exceeded(client: TestClient):
    conn_id = _create_mqtt_connection(client)
    fake_mgr = MagicMock()
    fake_mgr.subscribe = AsyncMock(side_effect=SubscriptionLimitExceededError(limit=64))
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            f"/api/v1/admin/connections/mqtt/{conn_id}/subscribe",
            json={"topic_filter": "t"},
        )
    assert resp.status_code == 409


def test_subscribe_route_wrong_protocol(client: TestClient):
    # Create an http connection, then try the mqtt subscribe route on it.
    resp = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http",
            "display_name": "rest-api",
            "tags": [],
            "credentials_backend": {"kind": "local"},
            "protocol_data": {
                "base_url": "https://api.example.com",
                "auth": {"kind": "none"},
                "operations": {},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    http_id = resp.json()["id"]
    fake_mgr = MagicMock()
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            f"/api/v1/admin/connections/mqtt/{http_id}/subscribe",
            json={"topic_filter": "t"},
        )
    assert resp.status_code == 400


# ── drain ────────────────────────────────────────────────────────────


def test_drain_route(client: TestClient):
    dr = DrainResult(events=[{"topic": "t", "payload": "p"}], returned=1, remaining=3,
                     overflow_dropped=2, oversized_dropped=0, global_pressure_dropped=0)
    fake_mgr = MagicMock()
    fake_mgr.drain = AsyncMock(return_value=dr)
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            "/api/v1/admin/connections/mqtt/subscriptions/sub-abc/drain",
            json={"max_events": 25},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["returned"] == 1
    assert body["remaining"] == 3
    assert body["overflow_dropped"] == 2
    fake_mgr.drain.assert_awaited_with("sub-abc", 25)


def test_drain_route_not_found(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.drain = AsyncMock(side_effect=SubscriptionNotFoundError(subscription_id="x"))
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.post(
            "/api/v1/admin/connections/mqtt/subscriptions/x/drain",
            json={},
        )
    assert resp.status_code == 404


# ── unsubscribe ──────────────────────────────────────────────────────


def test_unsubscribe_route(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.unsubscribe = AsyncMock(return_value=None)
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.delete("/api/v1/admin/connections/mqtt/subscriptions/sub-abc")
    assert resp.status_code == 204
    fake_mgr.unsubscribe.assert_awaited_with("sub-abc")


def test_unsubscribe_route_not_found(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.unsubscribe = AsyncMock(side_effect=SubscriptionNotFoundError(subscription_id="x"))
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.delete("/api/v1/admin/connections/mqtt/subscriptions/x")
    assert resp.status_code == 404


# ── list / stats ─────────────────────────────────────────────────────


def test_list_subscriptions_route(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.list_subscriptions = MagicMock(return_value=[_stats("sub-1"), _stats("sub-2")])
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.get("/api/v1/admin/connections/mqtt/subscriptions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    assert {s["subscription_id"] for s in body} == {"sub-1", "sub-2"}


def test_get_subscription_stats_route(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.stats = MagicMock(return_value=_stats("sub-7"))
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.get("/api/v1/admin/connections/mqtt/subscriptions/sub-7")
    assert resp.status_code == 200, resp.text
    assert resp.json()["subscription_id"] == "sub-7"
    assert resp.json()["buffer_depth"] == 2


def test_get_subscription_stats_not_found(client: TestClient):
    fake_mgr = MagicMock()
    fake_mgr.stats = MagicMock(side_effect=SubscriptionNotFoundError(subscription_id="x"))
    with patch(
        "sagewai.connections.protocols.mqtt.get_subscription_manager",
        return_value=fake_mgr,
    ):
        resp = client.get("/api/v1/admin/connections/mqtt/subscriptions/x")
    assert resp.status_code == 404
