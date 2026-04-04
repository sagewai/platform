"""Tests for the MCP agent server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.mcp.agent_server import (
    _build_tool,
    _chat_with_agent,
    _list_agents,
    _sanitize_tool_name,
    create_agent_server,
)


# ------------------------------------------------------------------
# _sanitize_tool_name
# ------------------------------------------------------------------


class TestSanitizeToolName:
    def test_simple_name(self) -> None:
        assert _sanitize_tool_name("knowledge_agent") == "sagewai_chat_knowledge_agent"

    def test_hyphenated_name(self) -> None:
        assert _sanitize_tool_name("knowledge-agent") == "sagewai_chat_knowledge_agent"

    def test_dotted_name(self) -> None:
        assert _sanitize_tool_name("my.agent") == "sagewai_chat_my_agent"

    def test_special_chars_stripped(self) -> None:
        assert _sanitize_tool_name("agent@v2!") == "sagewai_chat_agentv2"


# ------------------------------------------------------------------
# _list_agents (mocked HTTP)
# ------------------------------------------------------------------


class TestListAgents:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "agent-a"}, {"id": "agent-b"}],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            agents = await _list_agents("http://test:8000", "tok")

        assert len(agents) == 2
        assert agents[0]["id"] == "agent-a"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            agents = await _list_agents("http://test:8000", "tok")

        assert agents == []

    @pytest.mark.asyncio
    async def test_auth_header_sent(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            await _list_agents("http://test:8000", "secret-token")

        call_kwargs = mock_client.get.call_args
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_no_token_no_auth_header(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            await _list_agents("http://test:8000", "")

        call_kwargs = mock_client.get.call_args
        assert "Authorization" not in call_kwargs[1]["headers"]


# ------------------------------------------------------------------
# _chat_with_agent (mocked HTTP)
# ------------------------------------------------------------------


class TestChatWithAgent:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello from agent"}}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            result = await _chat_with_agent("http://test:8000", "tok", "my-agent", "hi")

        assert result == "Hello from agent"

    @pytest.mark.asyncio
    async def test_http_error_returns_message(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            result = await _chat_with_agent("http://test:8000", "tok", "my-agent", "hi")

        assert "Error communicating" in result

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("sagewai.mcp.agent_server.httpx.AsyncClient", return_value=mock_client):
            await _chat_with_agent("http://test:8000", "tok", "knowledge-agent", "query")

        call_kwargs = mock_client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["model"] == "knowledge-agent"
        assert payload["messages"][0]["content"] == "query"


# ------------------------------------------------------------------
# _build_tool
# ------------------------------------------------------------------


class TestBuildTool:
    def test_tool_name_and_schema(self) -> None:
        tool = _build_tool("http://test:8000", "tok", "knowledge-agent")
        assert tool.name == "sagewai_chat_knowledge_agent"
        assert "message" in tool.parameters["properties"]
        assert tool.handler is not None

    def test_tool_description(self) -> None:
        tool = _build_tool("http://test:8000", "tok", "scout")
        assert "scout" in tool.description


# ------------------------------------------------------------------
# create_agent_server
# ------------------------------------------------------------------


class TestCreateAgentServer:
    @pytest.mark.asyncio
    async def test_registers_tools_for_agents(self) -> None:
        with patch(
            "sagewai.mcp.agent_server._list_agents",
            return_value=[{"id": "agent-a"}, {"id": "agent-b"}],
        ):
            server = await create_agent_server(gateway_url="http://test:8000", token="tok")

        assert len(server.tools) == 2
        names = {t.name for t in server.tools}
        assert "sagewai_chat_agent_a" in names
        assert "sagewai_chat_agent_b" in names

    @pytest.mark.asyncio
    async def test_empty_agents(self) -> None:
        with patch("sagewai.mcp.agent_server._list_agents", return_value=[]):
            server = await create_agent_server(gateway_url="http://test:8000", token="tok")

        assert len(server.tools) == 0

    @pytest.mark.asyncio
    async def test_env_var_fallback(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"SAGEWAI_GATEWAY_URL": "http://env:9000", "SAGEWAI_GATEWAY_TOKEN": "env-tok"},
            ),
            patch("sagewai.mcp.agent_server._list_agents", return_value=[]) as mock_list,
        ):
            await create_agent_server()

        mock_list.assert_called_once_with("http://env:9000", "env-tok")

    @pytest.mark.asyncio
    async def test_skips_agents_without_id(self) -> None:
        with patch(
            "sagewai.mcp.agent_server._list_agents",
            return_value=[{"id": "valid"}, {"name": "no-id"}, {"id": ""}],
        ):
            server = await create_agent_server(gateway_url="http://test:8000", token="tok")

        assert len(server.tools) == 1
        assert server.tools[0].name == "sagewai_chat_valid"
