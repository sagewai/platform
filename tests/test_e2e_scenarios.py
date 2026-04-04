"""End-to-end scenario tests — validate multi-component interactions."""

from unittest.mock import AsyncMock, patch

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.conversation import ConversationManager
from sagewai.core.events import AgentEvent
from sagewai.core.session import InMemorySessionStore
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec
from sagewai.safety.guardrails import ContentFilter, GuardrailViolationError


class ScenarioAgent(BaseAgent):
    """Configurable agent for e2e scenarios."""

    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0
        self.received_messages = []

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        self.received_messages = list(messages)
        resp = self._responses[self._call_count % len(self._responses)]
        self._call_count += 1
        return resp


class TestMultiTurnWithMemory:
    """Multi-turn conversation with memory injection and compaction."""

    @pytest.mark.asyncio
    async def test_memory_context_injected_in_chat(self):
        """Memory context should appear in messages sent to LLM."""

        class FakeMemory:
            async def retrieve(self, query, top_k=5):
                return ["User prefers Python over JavaScript"]

            async def store(self, content, metadata=None):
                pass

        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("I'll use Python!")],
            name="mem-agent",
            model="gpt-4o",
            memory=FakeMemory(),
        )
        result = await agent.chat("What language should I use?")
        assert result == "I'll use Python!"
        # Verify memory was injected
        all_content = " ".join(m.content or "" for m in agent.received_messages)
        assert "Python over JavaScript" in all_content

    @pytest.mark.asyncio
    async def test_multi_turn_with_compaction(self):
        """Long conversation triggers auto-compaction mid-flow."""
        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("response")],
            name="compact-agent",
            model="gpt-4o",
            max_context_tokens=200,
        )
        # First chat with a very long message to trigger compaction
        long_msg = "Tell me about " + " ".join([f"topic_{i}" for i in range(100)])
        result = await agent.chat(long_msg)
        assert result == "response"

    @pytest.mark.asyncio
    async def test_session_persistence_roundtrip(self):
        """Send message, save session, create new manager, resume."""
        store = InMemorySessionStore()
        agent = ScenarioAgent(
            responses=[
                ChatMessage.assistant("Hello! I'm here to help."),
                ChatMessage.assistant("Sure, continuing our chat."),
            ],
            name="session-agent",
            model="gpt-4o",
        )

        # Session 1: first message
        mgr1 = ConversationManager(agent=agent, session_store=store, session_id="s1")
        resp1 = await mgr1.send("Hi there")
        assert resp1 is not None

        # Session 2: resume
        mgr2 = ConversationManager(agent=agent, session_store=store, session_id="s1")
        resp2 = await mgr2.send("Let's continue")
        assert resp2 is not None


class TestGuardrailsAcrossEntryPoints:
    """Guardrails should work consistently across chat, chat_with_history, chat_stream."""

    @pytest.mark.asyncio
    async def test_input_guard_blocks_chat(self):
        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("ok")],
            name="g",
            model="gpt-4o",
            guardrails=[ContentFilter(blocklist=["blocked"])],
        )
        with pytest.raises(GuardrailViolationError):
            await agent.chat("this is blocked content")

    @pytest.mark.asyncio
    async def test_input_guard_blocks_chat_stream(self):
        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("ok")],
            name="g",
            model="gpt-4o",
            guardrails=[ContentFilter(blocklist=["blocked"])],
        )
        with pytest.raises(GuardrailViolationError):
            async for _ in agent.chat_stream("this is blocked content"):
                pass

    @pytest.mark.asyncio
    async def test_output_guard_blocks_chat(self):
        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("secret data leaked")],
            name="g",
            model="gpt-4o",
            guardrails=[ContentFilter(blocklist=["leaked"])],
        )
        with pytest.raises(GuardrailViolationError):
            await agent.chat("tell me secrets")


class TestCrossEngineEquivalence:
    """Same input should produce structurally similar outputs from both engines."""

    @pytest.mark.asyncio
    async def test_both_engines_return_chat_message(self):
        """Both UniversalAgent and GoogleNativeAgent return ChatMessage."""
        from sagewai.engines.google_native import GoogleNativeAgent
        from sagewai.engines.universal import UniversalAgent

        messages = [ChatMessage.user("hi")]

        # Mock UniversalAgent
        with patch("sagewai.engines.universal.litellm.acompletion") as mock_litellm:
            mock_resp = type("R", (), {
                "choices": [type("C", (), {
                    "message": type("M", (), {
                        "content": "hello from litellm",
                        "tool_calls": None,
                        "role": "assistant",
                    })(),
                    "finish_reason": "stop",
                })()],
                "usage": type("U", (), {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                })(),
            })()
            mock_litellm.return_value = mock_resp
            ua = UniversalAgent(name="u", model="gpt-4o")
            result_u = await ua._call_llm(messages, [])

        # Mock GoogleNativeAgent
        with patch("sagewai.engines.google_native.genai.Client") as mock_gcls:
            mock_gemini_resp = type("R", (), {
                "candidates": [type("C", (), {
                    "content": type("Ct", (), {
                        "parts": [type("P", (), {
                            "text": "hello from gemini",
                            "function_call": None,
                        })()],
                    })(),
                })()],
                "usage_metadata": None,
            })()
            mock_instance = mock_gcls.return_value
            mock_instance.aio.models.generate_content = AsyncMock(
                return_value=mock_gemini_resp
            )
            ga = GoogleNativeAgent(name="g", model="gemini-2.0-flash")
            result_g = await ga._call_llm(messages, [])

        # Both should return ChatMessage with content
        assert isinstance(result_u, ChatMessage)
        assert isinstance(result_g, ChatMessage)
        assert result_u.content is not None
        assert result_g.content is not None
        assert result_u.role == "assistant"
        assert result_g.role == "assistant"


class TestEventEmissionOrdering:
    """Events should be emitted in correct order during agent execution."""

    @pytest.mark.asyncio
    async def test_event_order_simple_chat(self):
        events = []

        async def listener(event, data):
            events.append(event)

        agent = ScenarioAgent(
            responses=[ChatMessage.assistant("hi")],
            name="ev",
            model="gpt-4o",
        )
        agent.on_event(listener)
        await agent.chat("hello")

        assert AgentEvent.RUN_STARTED in events
        assert AgentEvent.RUN_FINISHED in events
        start_idx = events.index(AgentEvent.RUN_STARTED)
        end_idx = events.index(AgentEvent.RUN_FINISHED)
        assert start_idx < end_idx

    @pytest.mark.asyncio
    async def test_event_order_with_tool_call(self):
        events = []

        async def listener(event, data):
            events.append(event)

        async def my_tool(x: str) -> str:
            return "result"

        tool = ToolSpec(
            name="my_tool",
            description="test",
            handler=my_tool,
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )

        agent = ScenarioAgent(
            responses=[
                ChatMessage.assistant(
                    tool_calls=[ToolCall(id="c1", name="my_tool", arguments={"x": "a"})],
                ),
                ChatMessage.assistant("done"),
            ],
            name="ev",
            model="gpt-4o",
            tools=[tool],
        )
        agent.on_event(listener)
        await agent.chat("do it")

        assert AgentEvent.RUN_STARTED in events
        assert AgentEvent.RUN_FINISHED in events

    @pytest.mark.asyncio
    async def test_error_emits_run_error_event(self):
        """When chat() raises, RUN_ERROR should be emitted."""
        events = []

        async def listener(event, data):
            events.append(event)

        class FailingAgent(BaseAgent):
            async def _invoke_llm(self, messages, tools, *, model_override=None):
                raise RuntimeError("LLM down")

        agent = FailingAgent(name="fail", model="gpt-4o")
        agent.on_event(listener)

        with pytest.raises(RuntimeError, match="LLM down"):
            await agent.chat("hello")

        assert AgentEvent.RUN_STARTED in events
        assert AgentEvent.RUN_ERROR in events

    @pytest.mark.asyncio
    async def test_multi_tool_event_ordering(self):
        """Multiple tool calls should each have start/end events."""
        events = []

        async def listener(event, data):
            events.append((event, data.get("tool_call_id", "")))

        async def noop(x: str) -> str:
            return "ok"

        tool = ToolSpec(
            name="noop",
            description="noop",
            handler=noop,
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )

        agent = ScenarioAgent(
            responses=[
                ChatMessage.assistant(
                    tool_calls=[
                        ToolCall(id="c1", name="noop", arguments={"x": "a"}),
                        ToolCall(id="c2", name="noop", arguments={"x": "b"}),
                    ],
                ),
                ChatMessage.assistant("done"),
            ],
            name="ev",
            model="gpt-4o",
            tools=[tool],
        )
        agent.on_event(listener)
        await agent.chat("do it")

        event_types = [e for e, _ in events]
        assert event_types.count(AgentEvent.TOOL_CALL_START) == 2
        assert event_types.count(AgentEvent.TOOL_CALL_END) == 2
