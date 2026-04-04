"""Tests for streaming support (chat_stream, _stream_llm, _stream_agent_loop)."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec
from sagewai.safety.guardrails import ContentFilter, GuardrailViolationError


class StreamingMockAgent(BaseAgent):
    """Test agent that yields predetermined streaming chunks."""

    def __init__(self, stream_sequences: list[list[str | ToolCall]], **kwargs):
        super().__init__(**kwargs)
        self._stream_sequences = list(stream_sequences)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError("Streaming mock should not call _call_llm")

    async def _stream_llm(self, messages, tools, *, model_override=None):
        sequence = self._stream_sequences[self._call_count]
        self._call_count += 1
        for item in sequence:
            yield item


class FallbackMockAgent(BaseAgent):
    """Test agent that only implements _call_llm (no _stream_llm override)."""

    def __init__(self, responses: list[ChatMessage], **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


@pytest.mark.asyncio
async def test_stream_simple_text():
    """chat_stream yields text chunks from the LLM."""
    agent = StreamingMockAgent(
        stream_sequences=[["Hello", " ", "world", "!"]],
        name="test",
        model="mock",
    )
    chunks = []
    async for chunk in agent.chat_stream("Hi"):
        chunks.append(chunk)
    assert chunks == ["Hello", " ", "world", "!"]
    assert agent._call_count == 1


@pytest.mark.asyncio
async def test_stream_with_tool_calls():
    """chat_stream handles tool calls internally and continues streaming."""
    call_log = []

    async def mock_search(query: str) -> str:
        call_log.append(query)
        return "found it"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_search,
    )

    agent = StreamingMockAgent(
        stream_sequences=[
            # First response: tool call (no text)
            [ToolCall(id="tc1", name="search", arguments={"query": "test"})],
            # Second response: text after tool execution
            ["Found", " it!"],
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
    )

    chunks = []
    async for chunk in agent.chat_stream("Search for test"):
        chunks.append(chunk)

    assert chunks == ["Found", " it!"]
    assert agent._call_count == 2
    assert call_log == ["test"]


@pytest.mark.asyncio
async def test_stream_text_and_tool_calls_mixed():
    """Stream yields text before tool calls, then continues after tool execution."""

    async def mock_tool() -> str:
        return "done"

    tool_spec = ToolSpec(
        name="do_thing",
        description="Do thing",
        handler=mock_tool,
    )

    agent = StreamingMockAgent(
        stream_sequences=[
            # First: some text then a tool call
            ["Let me ", "check...", ToolCall(id="tc1", name="do_thing", arguments={})],
            # Second: final text after tool
            ["All done!"],
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
    )

    chunks = []
    async for chunk in agent.chat_stream("Do something"):
        chunks.append(chunk)

    assert chunks == ["Let me ", "check...", "All done!"]


@pytest.mark.asyncio
async def test_stream_max_iterations():
    """Streaming loop stops after max_iterations."""

    async def loop_tool() -> str:
        return "looping"

    tool_spec = ToolSpec(
        name="loop",
        description="Loop",
        handler=loop_tool,
    )

    agent = StreamingMockAgent(
        stream_sequences=[
            [ToolCall(id="tc1", name="loop", arguments={})],
            [ToolCall(id="tc2", name="loop", arguments={})],
            [ToolCall(id="tc3", name="loop", arguments={})],
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
        max_iterations=3,
    )

    chunks = []
    async for chunk in agent.chat_stream("Loop forever"):
        chunks.append(chunk)

    # No text yielded — all iterations were tool calls
    assert chunks == []
    assert agent._call_count == 3


@pytest.mark.asyncio
async def test_stream_fallback_to_call_llm():
    """Default _stream_llm falls back to _call_llm for non-streaming engines."""
    agent = FallbackMockAgent(
        responses=[ChatMessage.assistant("Full response at once")],
        name="test",
        model="mock",
    )

    chunks = []
    async for chunk in agent.chat_stream("Hi"):
        chunks.append(chunk)

    assert chunks == ["Full response at once"]
    assert agent._call_count == 1


@pytest.mark.asyncio
async def test_stream_fallback_with_tool_calls():
    """Fallback _stream_llm correctly handles tool calls from _call_llm."""

    async def mock_tool(x: str) -> str:
        return f"result: {x}"

    tool_spec = ToolSpec(
        name="proc",
        description="Process",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        handler=mock_tool,
    )

    agent = FallbackMockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="proc", arguments={"x": "hello"})]
            ),
            ChatMessage.assistant("Processed: result: hello"),
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
    )

    chunks = []
    async for chunk in agent.chat_stream("Process hello"):
        chunks.append(chunk)

    assert chunks == ["Processed: result: hello"]
    assert agent._call_count == 2


@pytest.mark.asyncio
async def test_stream_system_prompt():
    """chat_stream includes system prompt in messages."""
    captured = []

    class CapturingStreamAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            captured.extend(messages)
            return ChatMessage.assistant("OK")

    agent = CapturingStreamAgent(
        name="test",
        model="mock",
        system_prompt="Be concise",
    )

    async for _ in agent.chat_stream("Hello"):
        pass

    assert len(captured) == 2
    assert captured[0].content == "Be concise"
    assert captured[1].content == "Hello"


@pytest.mark.asyncio
async def test_stream_error_mid_iteration():
    """Exception during streaming should propagate to caller."""

    class ErrorStreamAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            return ChatMessage.assistant("fallback")

        async def _stream_llm(self, messages, tools, *, model_override=None):
            yield "hello "
            raise RuntimeError("Stream broke")

    agent = ErrorStreamAgent(name="t", model="gpt-4o")
    with pytest.raises(RuntimeError, match="Stream broke"):
        async for _ in agent.chat_stream("hi"):
            pass


@pytest.mark.asyncio
async def test_stream_text_tool_text_interleaving():
    """Handles text -> tool -> text pattern within a single stream."""

    async def add(a: int, b: int) -> str:
        return str(a + b)

    tool = ToolSpec(
        name="add",
        description="add",
        handler=add,
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        },
    )

    class InterleavedAgent(BaseAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._call_count = 0

        async def _invoke_llm(self, messages, tools, *, model_override=None):
            return ChatMessage.assistant("unused")

        async def _stream_llm(self, messages, tools, *, model_override=None):
            self._call_count += 1
            if self._call_count == 1:
                yield "Let me calculate: "
                yield ToolCall(id="c1", name="add", arguments={"a": 2, "b": 3})
            else:
                yield "The answer is 5."

    agent = InterleavedAgent(name="t", model="gpt-4o", tools=[tool])
    chunks = []
    async for chunk in agent.chat_stream("what is 2+3"):
        chunks.append(chunk)
    full = "".join(chunks)
    assert "5" in full


@pytest.mark.asyncio
async def test_stream_with_guardrails_output_check():
    """Output guardrails should be checked after streaming completes."""

    class SimpleStreamAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            return ChatMessage.assistant("bad_word")

        async def _stream_llm(self, messages, tools, *, model_override=None):
            yield "bad_word"

    agent = SimpleStreamAgent(
        name="t",
        model="gpt-4o",
        guardrails=[ContentFilter(blocklist=["bad_word"])],
    )
    with pytest.raises(GuardrailViolationError):
        chunks = []
        async for chunk in agent.chat_stream("hello"):
            chunks.append(chunk)


@pytest.mark.asyncio
async def test_stream_with_memory_injection():
    """chat_stream() should inject memory context into messages."""

    class MemoryStreamAgent(BaseAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._received_messages = []

        async def _invoke_llm(self, messages, tools, *, model_override=None):
            return ChatMessage.assistant("unused")

        async def _stream_llm(self, messages, tools, *, model_override=None):
            self._received_messages = messages
            yield "ok"

    class FakeMemory:
        async def retrieve(self, query, top_k=5):
            return ["remembered fact: sky is blue"]

        async def store(self, content, metadata=None):
            pass

    agent = MemoryStreamAgent(name="t", model="gpt-4o", memory=FakeMemory())
    chunks = []
    async for chunk in agent.chat_stream("what color is sky"):
        chunks.append(chunk)

    # Memory context should be injected into the messages
    all_content = " ".join(m.content or "" for m in agent._received_messages)
    assert "sky is blue" in all_content
