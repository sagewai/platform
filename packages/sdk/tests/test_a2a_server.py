# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for A2A server (agent card endpoint + JSON-RPC)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.protocols.a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
)
from sagewai.protocols.a2a.server import A2AServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _echo_handler(message: str) -> str:
    return f"Echo: {message}"


async def _error_handler(message: str) -> str:
    raise RuntimeError("Agent failed")


def _make_app(handler=_echo_handler, api_key=None) -> FastAPI:
    card = AgentCard(
        name="test-agent",
        description="A test agent",
        version="1.0.0",
        provider=AgentProvider(organization="TestOrg"),
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(id="echo", name="echo", description="Echo messages")],
    )
    a2a = A2AServer(card=card, handler=handler, api_key=api_key)
    app = FastAPI()
    app.include_router(a2a.router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


@pytest.fixture
def auth_client():
    return TestClient(_make_app(api_key="test-secret-key"))


@pytest.fixture
def error_client():
    return TestClient(_make_app(handler=_error_handler))


# ---------------------------------------------------------------------------
# Agent card endpoint tests
# ---------------------------------------------------------------------------


class TestAgentCardEndpoint:
    def test_agent_card_served(self, client):
        """GET /.well-known/agent-card.json returns the agent card."""
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-agent"
        assert data["description"] == "A test agent"
        assert data["version"] == "1.0.0"

    def test_agent_card_camel_case(self, client):
        """Agent card uses camelCase aliases."""
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert "defaultInputModes" in data
        assert "defaultOutputModes" in data

    def test_agent_card_has_skills(self, client):
        """Agent card includes registered skills."""
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["id"] == "echo"

    def test_agent_card_has_provider(self, client):
        """Agent card includes provider info."""
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert data["provider"]["organization"] == "TestOrg"

    def test_agent_card_has_capabilities(self, client):
        """Agent card includes capabilities."""
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        assert data["capabilities"]["streaming"] is True


# ---------------------------------------------------------------------------
# tasks/send tests
# ---------------------------------------------------------------------------


class TestTasksSend:
    def test_send_basic(self, client):
        """tasks/send creates and completes a task."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": "Hello"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "1"
        result = data["result"]
        assert result["status"]["state"] == "completed"
        assert result["artifacts"][0]["parts"][0]["text"] == "Echo: Hello"

    def test_send_with_custom_id(self, client):
        """tasks/send uses provided task ID."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/send",
                "params": {"id": "custom-task-id", "message": "Hi"},
            },
        )
        result = resp.json()["result"]
        assert result["id"] == "custom-task-id"

    def test_send_with_parts_message(self, client):
        """tasks/send extracts text from message parts."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tasks/send",
                "params": {
                    "message": {
                        "parts": [
                            {"type": "text", "text": "Hello"},
                            {"type": "text", "text": "World"},
                        ]
                    }
                },
            },
        )
        result = resp.json()["result"]
        assert result["artifacts"][0]["parts"][0]["text"] == "Echo: Hello World"

    def test_send_handler_error(self, error_client):
        """tasks/send returns error when handler fails."""
        resp = error_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "4",
                "method": "tasks/send",
                "params": {"message": "Fail"},
            },
        )
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32000
        assert "Agent failed" in data["error"]["message"]


# ---------------------------------------------------------------------------
# tasks/get tests
# ---------------------------------------------------------------------------


class TestTasksGet:
    def test_get_existing_task(self, client):
        """tasks/get returns a previously created task."""
        # First create a task
        send_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"id": "get-me", "message": "Hello"},
            },
        )
        assert send_resp.json()["result"]["id"] == "get-me"

        # Then get it
        get_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/get",
                "params": {"id": "get-me"},
            },
        )
        result = get_resp.json()["result"]
        assert result["id"] == "get-me"
        assert result["status"]["state"] == "completed"

    def test_get_nonexistent_task(self, client):
        """tasks/get returns error for unknown task."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/get",
                "params": {"id": "nonexistent"},
            },
        )
        data = resp.json()
        assert "error" in data
        assert "not found" in data["error"]["message"].lower()


# ---------------------------------------------------------------------------
# tasks/cancel tests
# ---------------------------------------------------------------------------


class TestTasksCancel:
    def test_cancel_existing_task(self, client):
        """tasks/cancel sets task state to canceled."""
        # Create a task
        client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"id": "cancel-me", "message": "Hello"},
            },
        )

        # Cancel it
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/cancel",
                "params": {"id": "cancel-me"},
            },
        )
        result = resp.json()["result"]
        assert result["status"]["state"] == "canceled"

    def test_cancel_nonexistent_task(self, client):
        """tasks/cancel returns error for unknown task."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/cancel",
                "params": {"id": "nonexistent"},
            },
        )
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# JSON-RPC error handling
# ---------------------------------------------------------------------------


class TestJsonRpcErrors:
    def test_unknown_method(self, client):
        """Unknown JSON-RPC method returns -32601."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "unknown/method",
            },
        )
        data = resp.json()
        assert data["error"]["code"] == -32601
        assert "not found" in data["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuth:
    def test_no_auth_required_by_default(self, client):
        """Requests succeed without auth when no API key configured."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": "Hello"},
            },
        )
        assert resp.status_code == 200

    def test_auth_required_when_configured(self, auth_client):
        """Requests fail without auth when API key is configured."""
        resp = auth_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": "Hello"},
            },
        )
        assert resp.status_code == 401

    def test_auth_with_valid_key(self, auth_client):
        """Requests succeed with correct API key."""
        resp = auth_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": "Hello"},
            },
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_auth_with_invalid_key(self, auth_client):
        """Requests fail with incorrect API key."""
        resp = auth_client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": "Hello"},
            },
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_agent_card_no_auth(self, auth_client):
        """Agent card endpoint is always public."""
        resp = auth_client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
