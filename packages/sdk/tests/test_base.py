"""Tests for BaseAgent and the agentic tool-calling loop."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage, Role, ToolCall
from sagewai.models.tool import ToolSpec
from sagewai.safety.guardrails import (
    ContentFilter,
    Guardrail,
    GuardrailResult,
    GuardrailViolationError,
)


class MockAgent(BaseAgent):
    """Test agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


@pytest.mark.asyncio
async def test_simple_text_response():
    """Agent returns text immediately without tool calls."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="test",
        model="mock",
    )
    result = await agent.chat("Hi")
    assert result == "Hello!"
    assert agent._call_count == 1


@pytest.mark.asyncio
async def test_tool_call_then_text():
    """Agent calls a tool, gets result, then returns text."""
    call_log = []

    async def mock_tool(query: str) -> str:
        call_log.append(query)
        return "Tool result: found it"

    tool_spec = ToolSpec(
        name="search",
        description="Search something",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_tool,
    )

    responses = [
        # First response: agent wants to call a tool
        ChatMessage.assistant(
            tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})]
        ),
        # Second response: after tool result, agent gives final answer
        ChatMessage.assistant("Based on my search: found it"),
    ]

    agent = MockAgent(
        responses=responses,
        name="test",
        model="mock",
        tools=[tool_spec],
    )

    result = await agent.chat("Search for test")
    assert result == "Based on my search: found it"
    assert agent._call_count == 2
    assert call_log == ["test"]


@pytest.mark.asyncio
async def test_sync_tool_handler():
    """Agent can use synchronous tool handlers."""

    def sync_add(a: int, b: int) -> str:
        return str(a + b)

    tool_spec = ToolSpec(
        name="add",
        description="Add numbers",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        handler=sync_add,
    )

    responses = [
        ChatMessage.assistant(
            tool_calls=[ToolCall(id="tc1", name="add", arguments={"a": 2, "b": 3})]
        ),
        ChatMessage.assistant("The sum is 5"),
    ]

    agent = MockAgent(
        responses=responses,
        name="calc",
        model="mock",
        tools=[tool_spec],
    )

    result = await agent.chat("What is 2+3?")
    assert result == "The sum is 5"


@pytest.mark.asyncio
async def test_unknown_tool():
    """Agent handles calls to unknown tools gracefully."""
    responses = [
        ChatMessage.assistant(tool_calls=[ToolCall(id="tc1", name="nonexistent", arguments={})]),
        ChatMessage.assistant("Sorry, that tool isn't available"),
    ]

    agent = MockAgent(
        responses=responses,
        name="test",
        model="mock",
    )

    result = await agent.chat("Use nonexistent tool")
    assert result == "Sorry, that tool isn't available"


@pytest.mark.asyncio
async def test_max_iterations_guard():
    """Agent stops after max_iterations to prevent infinite loops."""
    # Create responses that always request tool calls (infinite loop)
    responses = [
        ChatMessage.assistant(tool_calls=[ToolCall(id=f"tc{i}", name="loop", arguments={})])
        for i in range(20)
    ]

    async def loop_tool() -> str:
        return "looping"

    tool_spec = ToolSpec(
        name="loop",
        description="Loop forever",
        handler=loop_tool,
    )

    agent = MockAgent(
        responses=responses,
        name="looper",
        model="mock",
        tools=[tool_spec],
        max_iterations=3,
    )

    result = await agent.chat("Loop")
    assert "maximum iterations" in result.lower()
    assert agent._call_count == 3


@pytest.mark.asyncio
async def test_chat_with_history():
    """Agent works with explicit message history."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Continuing our conversation")],
        name="test",
        model="mock",
    )

    messages = [
        ChatMessage.system("You are helpful."),
        ChatMessage.user("Previous message"),
        ChatMessage.assistant("Previous response"),
        ChatMessage.user("New message"),
    ]

    result = await agent.chat_with_history(messages)
    assert result.content == "Continuing our conversation"
    assert result.role == Role.assistant


@pytest.mark.asyncio
async def test_tool_error_handling():
    """Agent handles tool execution errors."""

    async def failing_tool() -> str:
        raise ValueError("Something went wrong")

    tool_spec = ToolSpec(
        name="fail",
        description="Always fails",
        handler=failing_tool,
    )

    responses = [
        ChatMessage.assistant(tool_calls=[ToolCall(id="tc1", name="fail", arguments={})]),
        ChatMessage.assistant("The tool failed, but I recovered"),
    ]

    agent = MockAgent(
        responses=responses,
        name="test",
        model="mock",
        tools=[tool_spec],
    )

    result = await agent.chat("Try the failing tool")
    assert result == "The tool failed, but I recovered"


@pytest.mark.asyncio
async def test_system_prompt_in_chat():
    """System prompt is prepended to messages in chat()."""
    call_messages = []

    class CapturingAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            call_messages.extend(messages)
            return ChatMessage.assistant("OK")

    agent = CapturingAgent(
        name="test",
        model="mock",
        system_prompt="Be helpful",
    )

    await agent.chat("Hello")
    assert len(call_messages) == 2
    assert call_messages[0].role == Role.system
    assert call_messages[0].content == "Be helpful"
    assert call_messages[1].role == Role.user


@pytest.mark.asyncio
async def test_chat_stream_applies_input_guardrails():
    """chat_stream() should check input guardrails before streaming."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("ok")],
        name="t",
        model="gpt-4o",
        guardrails=[ContentFilter(blocklist=["forbidden"])],
    )
    with pytest.raises(GuardrailViolationError):
        async for _ in agent.chat_stream("forbidden word"):
            pass


@pytest.mark.asyncio
async def test_memory_retrieval_failure_is_graceful():
    """If memory.retrieve() raises, chat() should still return a response."""

    class BrokenMemory:
        async def retrieve(self, query, top_k=5):
            raise ConnectionError("Memory DB down")

        async def store(self, content, metadata=None):
            pass

    agent = MockAgent(
        responses=[ChatMessage.assistant("hello")],
        name="t",
        model="gpt-4o",
        memory=BrokenMemory(),
    )
    result = await agent.chat("hi")
    assert result == "hello"


@pytest.mark.asyncio
async def test_event_listener_exception_swallowed():
    """A failing event listener should not break the agent loop."""
    events_received = []

    async def bad_listener(event, data):
        events_received.append(event)
        raise ValueError("Listener crashed")

    agent = MockAgent(
        responses=[ChatMessage.assistant("ok")],
        name="t",
        model="gpt-4o",
    )
    agent.on_event(bad_listener)
    result = await agent.chat("hi")
    assert result == "ok"
    assert len(events_received) > 0


@pytest.mark.asyncio
async def test_tool_result_non_serializable():
    """Tool returning a non-JSON-serializable object should not crash."""

    class CustomObj:
        def __str__(self):
            return "custom_object"

    async def weird_tool(x: str) -> object:
        return CustomObj()

    tool = ToolSpec(
        name="weird",
        description="returns weird object",
        handler=weird_tool,
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="c1", name="weird", arguments={"x": "a"})],
            ),
            ChatMessage.assistant("done"),
        ],
        name="t",
        model="gpt-4o",
        tools=[tool],
    )
    result = await agent.chat("do it")
    assert result == "done"


@pytest.mark.asyncio
async def test_empty_tool_calls_list_treated_as_no_tools():
    """Response with tool_calls=[] should be treated same as tool_calls=None."""
    agent = MockAgent(
        responses=[ChatMessage(role="assistant", content="hi", tool_calls=[])],
        name="t",
        model="gpt-4o",
    )
    result = await agent.chat("hello")
    assert result == "hi"


@pytest.mark.asyncio
async def test_three_iteration_tool_loop():
    """Agent handles 3+ consecutive tool-call iterations correctly."""

    async def echo(msg: str) -> str:
        return f"echo:{msg}"

    tool = ToolSpec(
        name="echo",
        description="echo",
        handler=echo,
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"msg": "1"})],
            ),
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="c2", name="echo", arguments={"msg": "2"})],
            ),
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="c3", name="echo", arguments={"msg": "3"})],
            ),
            ChatMessage.assistant("all done"),
        ],
        name="t",
        model="gpt-4o",
        tools=[tool],
    )
    result = await agent.chat("go")
    assert result == "all done"


@pytest.mark.asyncio
async def test_guardrail_escalate_does_not_raise():
    """Guardrail with action='escalate' should emit event but not raise."""

    class EscalatingGuard(Guardrail):
        async def check_input(self, message, context):
            return GuardrailResult(
                passed=False, violation="needs review", action="escalate"
            )

        async def check_output(self, response, context):
            return GuardrailResult(passed=True)

    agent = MockAgent(
        responses=[ChatMessage.assistant("ok")],
        name="t",
        model="gpt-4o",
        guardrails=[EscalatingGuard()],
    )
    # Should NOT raise — escalate is not a block
    result = await agent.chat("check this")
    assert result == "ok"
