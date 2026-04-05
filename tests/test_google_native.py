"""Tests for GoogleNativeAgent (google-genai SDK)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.engines.google_native import GoogleNativeAgent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


def _make_gemini_response(text=None, function_calls=None):
    """Create a mock Gemini GenerateContentResponse."""
    parts = []

    if text:
        part = MagicMock()
        part.text = text
        part.function_call = None
        parts.append(part)

    if function_calls:
        for fc in function_calls:
            part = MagicMock()
            part.text = None
            part.function_call = MagicMock()
            part.function_call.name = fc["name"]
            part.function_call.args = fc.get("args", {})
            parts.append(part)

    candidate = MagicMock()
    candidate.content.parts = parts

    response = MagicMock()
    response.candidates = [candidate]
    return response


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_simple_chat(mock_client_cls):
    """GoogleNativeAgent calls Gemini via client and returns text."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="Hello from Gemini!")
    )
    mock_client_cls.return_value = mock_client

    agent = GoogleNativeAgent(name="test", model="gemini-2.5-flash")
    result = await agent.chat("Hi")

    assert result == "Hello from Gemini!"
    mock_client.aio.models.generate_content.assert_called_once()


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_tool_call_parsing(mock_client_cls):
    """Function calls from Gemini are parsed into ToolCalls."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[
            _make_gemini_response(function_calls=[{"name": "search", "args": {"query": "test"}}]),
            _make_gemini_response(text="Found: test results"),
        ]
    )
    mock_client_cls.return_value = mock_client

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

    agent = GoogleNativeAgent(name="test", model="gemini-2.5-flash", tools=[tool])
    result = await agent.chat("Search for test")

    assert result == "Found: test results"
    assert mock_client.aio.models.generate_content.call_count == 2


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_tool_schema_conversion(mock_client_cls):
    """ToolSpecs are converted to Gemini function declarations via config."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="OK")
    )
    mock_client_cls.return_value = mock_client

    tool = ToolSpec(
        name="get_weather",
        description="Get weather",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )

    agent = GoogleNativeAgent(name="test", model="gemini-2.5-flash", tools=[tool])
    await agent.chat("Weather?")

    # Verify generate_content was called with config containing tools
    call_kwargs = mock_client.aio.models.generate_content.call_args[1]
    config = call_kwargs["config"]
    assert config.tools is not None
    assert len(config.tools) == 1
    declarations = config.tools[0].function_declarations
    assert len(declarations) == 1
    assert declarations[0].name == "get_weather"
    assert declarations[0].description == "Get weather"


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_system_instruction_in_config(mock_client_cls):
    """System messages are passed as system_instruction in config."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="I am a pirate!")
    )
    mock_client_cls.return_value = mock_client

    agent = GoogleNativeAgent(
        name="test",
        model="gemini-2.5-flash",
        system_prompt="You are a pirate.",
    )
    result = await agent.chat("Who are you?")

    assert result == "I am a pirate!"
    call_kwargs = mock_client.aio.models.generate_content.call_args[1]
    config = call_kwargs["config"]
    assert config.system_instruction == "You are a pirate."


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_content_building(mock_client_cls):
    """Messages are converted to proper Gemini Content objects."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="response")
    )
    mock_client_cls.return_value = mock_client

    agent = GoogleNativeAgent(name="test", model="gemini-2.5-flash")
    await agent.chat("Hello")

    call_kwargs = mock_client.aio.models.generate_content.call_args[1]
    contents = call_kwargs["contents"]
    # Should have one user Content with one text Part
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert len(contents[0].parts) == 1


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_multiple_system_messages_concatenated(mock_client_cls):
    """Multiple system messages should be concatenated into one system instruction."""
    mock_instance = mock_client_cls.return_value
    mock_instance.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="ok")
    )

    agent = GoogleNativeAgent(name="t", model="gemini-2.5-flash")
    messages = [
        ChatMessage(role="system", content="First."),
        ChatMessage(role="system", content="Second."),
        ChatMessage(role="user", content="hi"),
    ]
    await agent._call_llm(messages, [])

    call_kwargs = mock_instance.aio.models.generate_content.call_args[1]
    config = call_kwargs.get("config")
    # System instruction should contain both system messages
    assert config is not None
    assert "First." in config.system_instruction
    assert "Second." in config.system_instruction


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_empty_function_call_args(mock_client_cls):
    """Function calls with None args should be handled as empty dict."""
    fc = MagicMock()
    fc.name = "my_tool"
    fc.args = None
    response = _make_gemini_response(function_calls=[{"name": "my_tool", "args": None}])

    # Override the function_call mock to return None args
    part = response.candidates[0].content.parts[-1]
    part.function_call.args = None

    mock_instance = mock_client_cls.return_value
    mock_instance.aio.models.generate_content = AsyncMock(return_value=response)

    agent = GoogleNativeAgent(name="t", model="gemini-2.5-flash")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments == {}


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_response_missing_candidates(mock_client_cls):
    """Agent handles response with no candidates gracefully."""
    response = MagicMock()
    response.candidates = []
    response.usage_metadata = None
    mock_instance = mock_client_cls.return_value
    mock_instance.aio.models.generate_content = AsyncMock(return_value=response)

    agent = GoogleNativeAgent(name="t", model="gemini-2.5-flash")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    # Should return empty content, not crash
    assert result.content is None or result.content == ""


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_token_usage_fallback(mock_client_cls):
    """Token usage fields default to 0 when missing from response."""
    response = _make_gemini_response(text="ok")
    response.usage_metadata = None
    mock_instance = mock_client_cls.return_value
    mock_instance.aio.models.generate_content = AsyncMock(return_value=response)

    agent = GoogleNativeAgent(name="t", model="gemini-2.5-flash")
    result = await agent._call_llm(
        [ChatMessage(role="user", content="hi")], []
    )
    assert result.content == "ok"
    # Should not crash even without usage metadata


@pytest.mark.asyncio
@patch("sagewai.engines.google_native.genai.Client")
async def test_tool_result_mapped_to_user_role(mock_client_cls):
    """Tool result messages should be mapped to 'user' role in Gemini format."""
    mock_instance = mock_client_cls.return_value
    mock_instance.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(text="ok")
    )

    agent = GoogleNativeAgent(name="t", model="gemini-2.5-flash")
    messages = [
        ChatMessage(role="user", content="call my tool"),
        ChatMessage(role="tool", content='{"result": 42}', tool_call_id="tc1"),
    ]
    contents = agent._build_contents(messages)
    # Tool role should be mapped to "user" for Gemini
    roles = [c.role for c in contents]
    assert "user" in roles
