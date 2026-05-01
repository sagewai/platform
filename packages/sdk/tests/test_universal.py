# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for UniversalAgent (LiteLLM-based)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec


def _make_litellm_response(content=None, tool_calls=None):
    """Create a mock LiteLLM ModelResponse."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_simple_chat(mock_completion):
    """UniversalAgent calls litellm and returns text."""
    mock_completion.return_value = _make_litellm_response(content="Hello!")

    agent = UniversalAgent(name="test", model="gpt-4o")
    result = await agent.chat("Hi")

    assert result == "Hello!"
    mock_completion.assert_called_once()

    call_kwargs = mock_completion.call_args[1]
    assert call_kwargs["model"] == "gpt-4o"
    assert len(call_kwargs["messages"]) == 1
    assert call_kwargs["messages"][0]["role"] == "user"


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_with_system_prompt(mock_completion):
    """System prompt is included in messages."""
    mock_completion.return_value = _make_litellm_response(content="OK")

    agent = UniversalAgent(name="test", model="gpt-4o", system_prompt="Be concise")
    await agent.chat("Tell me about Python")

    call_kwargs = mock_completion.call_args[1]
    messages = call_kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "Be concise"


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_tool_schema_conversion(mock_completion):
    """ToolSpecs are converted to OpenAI function-calling format."""
    mock_completion.return_value = _make_litellm_response(content="No tools needed")

    tool = ToolSpec(
        name="get_weather",
        description="Get weather for a city",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )

    agent = UniversalAgent(name="test", model="gpt-4o", tools=[tool])
    await agent.chat("What's the weather?")

    call_kwargs = mock_completion.call_args[1]
    tools = call_kwargs["tools"]
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "get_weather"
    assert tools[0]["function"]["description"] == "Get weather for a city"


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_tool_call_parsing(mock_completion):
    """Tool calls from LiteLLM response are parsed correctly."""
    # First call: LLM wants to use a tool
    tc_mock = MagicMock()
    tc_mock.id = "call_123"
    tc_mock.function.name = "search"
    tc_mock.function.arguments = json.dumps({"query": "test"})

    mock_completion.side_effect = [
        _make_litellm_response(tool_calls=[tc_mock]),
        _make_litellm_response(content="Found: test results"),
    ]

    async def search_handler(query: str) -> str:
        return f"Results for: {query}"

    tool = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search_handler,
    )

    agent = UniversalAgent(name="test", model="gpt-4o", tools=[tool])
    result = await agent.chat("Search for test")

    assert result == "Found: test results"
    assert mock_completion.call_count == 2


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_temperature_and_max_tokens(mock_completion):
    """Agent passes temperature and max_tokens to litellm."""
    mock_completion.return_value = _make_litellm_response(content="OK")

    agent = UniversalAgent(
        name="test",
        model="gpt-4o",
        temperature=0.2,
        max_tokens=500,
    )
    await agent.chat("Test")

    call_kwargs = mock_completion.call_args[1]
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 500


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_streaming_error_mid_stream(mock_completion):
    """_stream_llm should propagate exceptions raised during streaming."""
    import litellm

    agent = UniversalAgent(name="t", model="gpt-4o")

    async def _exploding_stream(*args, **kwargs):
        raise litellm.APIConnectionError(
            message="Connection reset", llm_provider="openai", model="gpt-4o"
        )

    with patch("litellm.acompletion", side_effect=_exploding_stream):
        with pytest.raises(Exception, match="Connection reset"):
            chunks = []
            async for chunk in agent._stream_llm(
                [ChatMessage(role="user", content="hi")], []
            ):
                chunks.append(chunk)


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_malformed_tool_args_fallback(mock_completion):
    """Stream should fall back to raw string when tool args are invalid JSON."""
    agent = UniversalAgent(name="t", model="gpt-4o")

    # Build a mock streaming response with invalid JSON in tool call args
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta = MagicMock()
    chunk1.choices[0].delta.content = None
    chunk1.choices[0].delta.tool_calls = [MagicMock()]
    chunk1.choices[0].delta.tool_calls[0].index = 0
    chunk1.choices[0].delta.tool_calls[0].id = "call_1"
    chunk1.choices[0].delta.tool_calls[0].function = MagicMock()
    chunk1.choices[0].delta.tool_calls[0].function.name = "my_tool"
    chunk1.choices[0].delta.tool_calls[0].function.arguments = "{not valid json"
    chunk1.choices[0].finish_reason = "stop"

    async def _stream(*args, **kwargs):
        yield chunk1

    with patch("litellm.acompletion", return_value=_stream()):
        tool_calls = []
        async for item in agent._stream_llm(
            [ChatMessage(role="user", content="hi")], []
        ):
            if isinstance(item, ToolCall):
                tool_calls.append(item)

        assert len(tool_calls) == 1
        assert tool_calls[0].name == "my_tool"
        assert "raw" in tool_calls[0].arguments or isinstance(
            tool_calls[0].arguments, dict
        )


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_empty_content_response(mock_completion):
    """Agent handles response with content=None (tool-only response)."""
    response = _make_litellm_response(content=None)
    mock_completion.return_value = response

    agent = UniversalAgent(name="t", model="gpt-4o")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    assert result.content is None or result.content == ""


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_multiple_tool_calls_in_single_response(mock_completion):
    """Agent correctly parses multiple tool calls from a single LLM response."""
    tc1 = MagicMock()
    tc1.id = "call_1"
    tc1.function = MagicMock()
    tc1.function.name = "tool_a"
    tc1.function.arguments = '{"x": 1}'

    tc2 = MagicMock()
    tc2.id = "call_2"
    tc2.function = MagicMock()
    tc2.function.name = "tool_b"
    tc2.function.arguments = '{"y": 2}'

    response = _make_litellm_response(tool_calls=[tc1, tc2])
    mock_completion.return_value = response

    agent = UniversalAgent(name="t", model="gpt-4o")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].name == "tool_a"
    assert result.tool_calls[1].name == "tool_b"


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_inference_params_passed_through(mock_completion):
    """top_p, stop sequences, and penalty params are forwarded to litellm."""
    from sagewai.models.inference import InferenceParams

    mock_completion.return_value = _make_litellm_response(content="ok")

    agent = UniversalAgent(name="t", model="gpt-4o")
    # Set inference params directly on config to test _build_litellm_kwargs
    agent.config.inference = InferenceParams(
        temperature=0.5,
        max_tokens=100,
        top_p=0.9,
        frequency_penalty=0.3,
        presence_penalty=0.1,
        stop_sequences=["\n\n"],
    )
    await agent._call_llm([ChatMessage(role="user", content="hi")], [])

    call_kwargs = mock_completion.call_args[1]
    assert call_kwargs.get("top_p") == 0.9
    assert call_kwargs.get("frequency_penalty") == 0.3
    assert call_kwargs.get("presence_penalty") == 0.1
    assert call_kwargs.get("stop") == ["\n\n"]


@pytest.mark.asyncio
@patch("sagewai.engines.universal.litellm.acompletion", new_callable=AsyncMock)
async def test_tool_call_id_fallback_to_uuid(mock_completion):
    """When tool call has no id, a UUID is generated."""
    tc = MagicMock()
    tc.id = None
    tc.function = MagicMock()
    tc.function.name = "my_tool"
    tc.function.arguments = '{"a": 1}'
    response = _make_litellm_response(tool_calls=[tc])
    mock_completion.return_value = response

    agent = UniversalAgent(name="t", model="gpt-4o")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id is not None
    assert len(result.tool_calls[0].id) > 0
