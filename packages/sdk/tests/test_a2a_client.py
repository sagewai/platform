# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for A2A client (discovery + task delegation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sagewai.protocols.a2a.client import A2AClient, A2AError
from sagewai.protocols.a2a.models import AgentCard, AgentSkill
from sagewai.protocols.a2a.server import TaskState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DUMMY_REQUEST = httpx.Request("GET", "https://agent.example.com")


def _mock_card_response() -> dict:
    card = AgentCard(
        name="remote-agent",
        description="A remote agent",
        skills=[AgentSkill(id="search", name="search", description="Search the web")],
    )
    return card.model_dump(by_alias=True, exclude_none=True)


def _resp(status: int, json: dict) -> httpx.Response:
    """Create an httpx.Response with a request attached (needed for raise_for_status)."""
    return httpx.Response(status, json=json, request=_DUMMY_REQUEST)


def _mock_jsonrpc_result(result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": "1", "result": result}


def _mock_jsonrpc_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": "1", "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestDiscover:
    @pytest.mark.asyncio
    async def test_discover_fetches_agent_card(self):
        """discover() fetches and parses agent card."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _resp(200, _mock_card_response())
            async with A2AClient() as client:
                card = await client.discover("https://agent.example.com")

        assert card.name == "remote-agent"
        assert card.description == "A remote agent"
        assert len(card.skills) == 1
        mock_get.assert_called_once_with("https://agent.example.com/.well-known/agent-card.json")

    @pytest.mark.asyncio
    async def test_discover_strips_trailing_slash(self):
        """discover() handles trailing slash in base URL."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _resp(200, _mock_card_response())
            async with A2AClient() as client:
                await client.discover("https://agent.example.com/")

        mock_get.assert_called_once_with("https://agent.example.com/.well-known/agent-card.json")

    @pytest.mark.asyncio
    async def test_discover_propagates_http_error(self):
        """discover() raises on HTTP errors."""
        mock_resp = httpx.Response(404, request=_DUMMY_REQUEST)

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            async with A2AClient() as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.discover("https://agent.example.com")


# ---------------------------------------------------------------------------
# send_task tests
# ---------------------------------------------------------------------------


class TestSendTask:
    @pytest.mark.asyncio
    async def test_send_task_basic(self):
        """send_task() sends JSON-RPC and returns Task."""
        task_result = {
            "id": "task-1",
            "status": {"state": "completed"},
            "artifacts": [{"name": "response", "parts": [{"type": "text", "text": "Done"}]}],
        }
        mock_resp = _resp(200, _mock_jsonrpc_result(task_result))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                task = await client.send_task("https://agent.example.com", "Hello")

        assert task.id == "task-1"
        assert task.status.state == TaskState.COMPLETED
        assert task.artifacts[0].parts[0]["text"] == "Done"

    @pytest.mark.asyncio
    async def test_send_task_with_custom_id(self):
        """send_task() passes task_id in params."""
        task_result = {"id": "custom-id", "status": {"state": "completed"}, "artifacts": []}
        mock_resp = _resp(200, _mock_jsonrpc_result(task_result))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                task = await client.send_task(
                    "https://agent.example.com", "Hello", task_id="custom-id"
                )

        assert task.id == "custom-id"
        # Verify params included id
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["params"]["id"] == "custom-id"

    @pytest.mark.asyncio
    async def test_send_task_raises_on_rpc_error(self):
        """send_task() raises A2AError on JSON-RPC error."""
        mock_resp = _resp(200, _mock_jsonrpc_error(-32000, "Agent failed"))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                with pytest.raises(A2AError) as exc_info:
                    await client.send_task("https://agent.example.com", "Fail")

        assert exc_info.value.code == -32000
        assert "Agent failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_task tests
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_task(self):
        """get_task() retrieves task status."""
        task_result = {"id": "task-1", "status": {"state": "working"}, "artifacts": []}
        mock_resp = _resp(200, _mock_jsonrpc_result(task_result))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                task = await client.get_task("https://agent.example.com", "task-1")

        assert task.id == "task-1"
        assert task.status.state == TaskState.WORKING

    @pytest.mark.asyncio
    async def test_get_task_rpc_error(self):
        """get_task() raises on not-found error."""
        mock_resp = _resp(200, _mock_jsonrpc_error(-32000, "Task not found: bad-id"))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                with pytest.raises(A2AError):
                    await client.get_task("https://agent.example.com", "bad-id")


# ---------------------------------------------------------------------------
# cancel_task tests
# ---------------------------------------------------------------------------


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_cancel_task(self):
        """cancel_task() cancels and returns updated task."""
        task_result = {"id": "task-1", "status": {"state": "canceled"}, "artifacts": []}
        mock_resp = _resp(200, _mock_jsonrpc_result(task_result))

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            async with A2AClient() as client:
                task = await client.cancel_task("https://agent.example.com", "task-1")

        assert task.status.state == TaskState.CANCELED


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestClientAuth:
    @pytest.mark.asyncio
    async def test_api_key_sent_in_headers(self):
        """A2AClient sends Bearer token when api_key is set."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _resp(200, _mock_card_response())
            async with A2AClient(api_key="my-secret") as client:
                await client.discover("https://agent.example.com")

        # Verify the client was created with auth header
        assert client._client.headers.get("authorization") == "Bearer my-secret"

    @pytest.mark.asyncio
    async def test_no_auth_header_without_key(self):
        """A2AClient does not send auth header when no api_key."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _resp(200, _mock_card_response())
            async with A2AClient() as client:
                await client.discover("https://agent.example.com")

        assert "authorization" not in client._client.headers


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """A2AClient works as async context manager."""
        async with A2AClient() as client:
            assert client._client is not None

    @pytest.mark.asyncio
    async def test_close(self):
        """close() shuts down the HTTP client."""
        client = A2AClient()
        await client.close()
        assert client._client.is_closed
