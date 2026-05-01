# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Tests for AgentExecutor tool-call loop (Convergence Item 1b).

These tests verify:

- Agents with no tools — existing harness + direct paths unchanged.
- Agent with one tool: LLM calls it once, then returns a final response.
- Agent with multiple tool calls in a single turn, all executed.
- Max-iteration cap: loop stops when the LLM keeps requesting tools.
- Unknown tool name in agent.tools raises KeyError at spec resolution.
- Sync callable is accepted (wrapped via asyncio.to_thread).
- StepResult.tool_calls populated correctly; None when no tools used.
- Messages carry full multi-turn conversation (system+user+assistant+tool).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent
from sagewai.autopilot.controller.executor import AgentExecutor, ExecutorConfig
from sagewai.autopilot.controller.tool_registry import ToolRegistry
from sagewai.harness.models import HarnessIdentity


# ── Helpers ─────────────────────────────────────────────────────────────────


def _llm_agent(
    node_id: str = "scout",
    prompt_ref: str = "p/test.md",
    tools: tuple[str, ...] = (),
) -> Agent:
    return Agent(id=node_id, kind=AgentKind.LLM, prompt_ref=prompt_ref, tools=tools)


def _identity() -> HarnessIdentity:
    return HarnessIdentity(key_id="autopilot-default", user_id="autopilot")


def _harness_response(
    text: str = "final answer",
    model: str = "claude-haiku-4-5-20251001",
    tool_calls: list[dict] | None = None,
) -> dict:
    """Build a HarnessProxy response dict (OpenAI-compat shape)."""
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["content"] = None  # typically None when tool_calls present
        message["tool_calls"] = tool_calls

    return {
        "choices": [{"message": message, "finish_reason": "stop" if not tool_calls else "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "model": model,
        "_harness": {"tier": "simple", "policy_applied": None, "cost_usd": 0.001, "latency_ms": 100.0},
    }


def _tool_call_dict(name: str, arguments: dict, call_id: str = "call_001") -> dict:
    """Build a single tool_calls entry in OpenAI wire format."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _make_registry(*names: str) -> ToolRegistry:
    """Build a ToolRegistry with async no-op tools for the given names."""
    registry = ToolRegistry()
    for name in names:
        async def _noop(**kwargs: object) -> str:
            return f"result_of_{name}"

        registry.register(
            name=name,
            description=f"Test tool {name}",
            parameters={"type": "object", "properties": {}, "required": []},
            callable_=_noop,
        )
    return registry


# ── No-tools regression: harness path ────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_tools_harness_path_unchanged():
    """Agent with no tools — harness path unchanged, tool_calls is None."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response(text="done"))

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={"goal": "test"})

    proxy.handle_request.assert_awaited_once()
    assert result.status == "completed"
    assert result.output == "done"
    # No tools — field must be None (not an empty tuple)
    assert result.tool_calls is None


@pytest.mark.asyncio
async def test_no_tools_harness_messages_still_three_turns():
    """No-tools harness path: messages = system + user + assistant (3 turns)."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response(text="ok"))

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    result = await AgentExecutor(cfg).execute(_llm_agent(), context={"k": "v"})

    assert result.messages is not None
    assert len(result.messages) == 3
    assert [m["role"] for m in result.messages] == ["system", "user", "assistant"]


# ── Single tool call — harness path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_tool_call_harness_path():
    """LLM requests one tool, executor calls it, LLM returns final answer."""
    tool_called_with: list[dict] = []

    async def my_tool(service: str) -> str:
        tool_called_with.append({"service": service})
        return "metrics: cpu=42%"

    registry = ToolRegistry()
    registry.register(
        name="get_metrics",
        description="Fetch service metrics.",
        parameters={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
        callable_=my_tool,
    )

    # First response: tool call
    tc = _tool_call_dict("get_metrics", {"service": "api"})
    first_resp = _harness_response(tool_calls=[tc])
    # Second response: final answer
    second_resp = _harness_response(text="cpu is 42 percent, all good")

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(side_effect=[first_resp, second_resp])

    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
    )
    result = await AgentExecutor(cfg).execute(
        _llm_agent(tools=("get_metrics",)),
        context={"goal": "check api health"},
    )

    assert result.status == "completed"
    assert result.output == "cpu is 42 percent, all good"
    assert result.tool_calls == ("get_metrics",)
    assert tool_called_with == [{"service": "api"}]
    assert proxy.handle_request.await_count == 2


@pytest.mark.asyncio
async def test_single_tool_call_messages_include_tool_turns():
    """After a tool call, messages carry system+user+assistant+tool+assistant."""
    tc = _tool_call_dict("get_metrics", {"service": "api"})
    first_resp = _harness_response(tool_calls=[tc])
    second_resp = _harness_response(text="all done")

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(side_effect=[first_resp, second_resp])

    registry = _make_registry("get_metrics")
    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
    )
    result = await AgentExecutor(cfg).execute(
        _llm_agent(tools=("get_metrics",)),
        context={},
    )

    assert result.messages is not None
    roles = [m["role"] for m in result.messages]
    # system, user, assistant(with tool_calls), tool result, assistant(final)
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


# ── Multiple tool calls in a single turn — harness path ──────────────────────


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_single_turn():
    """LLM requests two tools in one turn; both are executed."""
    order: list[str] = []

    async def tool_a() -> str:
        order.append("a")
        return "a_result"

    async def tool_b() -> str:
        order.append("b")
        return "b_result"

    registry = ToolRegistry()
    registry.register("tool_a", "Tool A", {"type": "object", "properties": {}, "required": []}, tool_a)
    registry.register("tool_b", "Tool B", {"type": "object", "properties": {}, "required": []}, tool_b)

    tc_a = _tool_call_dict("tool_a", {}, call_id="c1")
    tc_b = _tool_call_dict("tool_b", {}, call_id="c2")
    first_resp = _harness_response(tool_calls=[tc_a, tc_b])
    second_resp = _harness_response(text="both done")

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(side_effect=[first_resp, second_resp])

    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
    )
    result = await AgentExecutor(cfg).execute(
        _llm_agent(tools=("tool_a", "tool_b")),
        context={},
    )

    assert result.status == "completed"
    assert result.tool_calls == ("tool_a", "tool_b")
    assert order == ["a", "b"]
    assert proxy.handle_request.await_count == 2


# ── Max-iteration cap ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_tool_iterations_cap():
    """Loop stops at max_tool_iterations even if LLM keeps requesting tools."""
    registry = _make_registry("counter_tool")

    # Every response is a tool call — model never returns final text.
    tc = _tool_call_dict("counter_tool", {})
    always_tool_resp = _harness_response(tool_calls=[tc])

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=always_tool_resp)

    max_iters = 3
    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
        max_tool_iterations=max_iters,
    )
    result = await AgentExecutor(cfg).execute(
        _llm_agent(tools=("counter_tool",)),
        context={},
    )

    # We do max_iters iterations (each time: call LLM, execute tool).
    # The loop fires max_iters times, so the proxy is called max_iters times.
    assert proxy.handle_request.await_count == max_iters
    # tool_calls accumulates one per iteration
    assert result.tool_calls is not None
    assert len(result.tool_calls) == max_iters
    assert all(n == "counter_tool" for n in result.tool_calls)
    # Status is completed (we reached cap, last assistant message had content=None)
    assert result.status == "completed"


# ── Unknown tool name ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_tool_name_raises_on_execute():
    """Agent declares a tool not in the registry — KeyError on spec resolution."""
    registry = ToolRegistry()  # empty

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response())

    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
    )
    with pytest.raises(KeyError, match="not registered"):
        await AgentExecutor(cfg).execute(
            _llm_agent(tools=("nonexistent_tool",)),
            context={},
        )


# ── Sync callable wrapped in asyncio.to_thread ───────────────────────────────


@pytest.mark.asyncio
async def test_sync_callable_accepted_by_registry():
    """Sync callables are executed via asyncio.to_thread without error."""
    def sync_tool(value: int) -> str:
        return f"doubled={value * 2}"

    registry = ToolRegistry()
    registry.register(
        "double",
        "Doubles an integer.",
        {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        sync_tool,
    )

    tc = _tool_call_dict("double", {"value": 7})
    first_resp = _harness_response(tool_calls=[tc])
    second_resp = _harness_response(text="doubled result received")

    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(side_effect=[first_resp, second_resp])

    cfg = ExecutorConfig(
        harness_proxy=proxy,
        harness_identity=_identity(),
        tool_registry=registry,
    )
    result = await AgentExecutor(cfg).execute(
        _llm_agent(tools=("double",)),
        context={},
    )

    assert result.status == "completed"
    assert result.tool_calls == ("double",)

    # Verify the tool result was fed back (it shows up in messages)
    assert result.messages is not None
    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "doubled=14" in tool_msgs[0]["content"]


# ── Direct-litellm path: no-tools regression ──────────────────────────────────


@pytest.mark.asyncio
async def test_no_tools_direct_path_unchanged():
    """Agent with no tools — direct-litellm path unchanged, tool_calls is None."""
    from unittest.mock import patch

    cfg = ExecutorConfig()  # no harness, no registry
    executor = AgentExecutor(cfg)
    agent = _llm_agent()  # no tools

    fake_message = MagicMock()
    fake_message.content = "direct text"
    fake_message.tool_calls = None
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response)):
        result = await executor.execute(agent, context={})

    assert result.status == "completed"
    assert result.output_preview == "direct text"
    assert result.tool_calls is None


# ── Direct-litellm path: single tool call ────────────────────────────────────


@pytest.mark.asyncio
async def test_single_tool_call_direct_path():
    """Direct-litellm fallback: LLM requests one tool, executor calls it."""
    from unittest.mock import call, patch

    tool_result_holder: list[str] = []

    async def my_tool(x: int) -> str:
        tool_result_holder.append(f"x={x}")
        return f"computed={x * 3}"

    registry = ToolRegistry()
    registry.register(
        "compute",
        "Compute a value.",
        {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        my_tool,
    )

    cfg = ExecutorConfig(tool_registry=registry)
    executor = AgentExecutor(cfg)

    # Build mock litellm responses
    # First: a response with tool_calls
    tc_raw = MagicMock()
    tc_raw.id = "call_x"
    tc_raw.function.name = "compute"
    tc_raw.function.arguments = json.dumps({"x": 4})

    first_message = MagicMock()
    first_message.content = ""
    first_message.tool_calls = [tc_raw]
    first_choice = MagicMock()
    first_choice.message = first_message
    first_resp = MagicMock()
    first_resp.choices = [first_choice]

    # Second: final response, no tool_calls
    second_message = MagicMock()
    second_message.content = "final direct answer"
    second_message.tool_calls = None
    second_choice = MagicMock()
    second_choice.message = second_message
    second_resp = MagicMock()
    second_resp.choices = [second_choice]

    with patch("litellm.acompletion", new=AsyncMock(side_effect=[first_resp, second_resp])):
        result = await executor.execute(
            _llm_agent(tools=("compute",)),
            context={},
        )

    assert result.status == "completed"
    assert result.output_preview == "final direct answer"
    assert result.tool_calls == ("compute",)
    assert tool_result_holder == ["x=4"]


# ── Direct-litellm path: max-iteration cap ───────────────────────────────────


@pytest.mark.asyncio
async def test_max_tool_iterations_cap_direct_path():
    """Direct-litellm: loop stops at max_tool_iterations."""
    from unittest.mock import patch

    registry = _make_registry("loop_tool")

    tc_raw = MagicMock()
    tc_raw.id = "call_loop"
    tc_raw.function.name = "loop_tool"
    tc_raw.function.arguments = "{}"

    always_tool_msg = MagicMock()
    always_tool_msg.content = ""
    always_tool_msg.tool_calls = [tc_raw]
    always_choice = MagicMock()
    always_choice.message = always_tool_msg
    always_resp = MagicMock()
    always_resp.choices = [always_choice]

    max_iters = 2
    cfg = ExecutorConfig(tool_registry=registry, max_tool_iterations=max_iters)
    executor = AgentExecutor(cfg)

    mock_acompletion = AsyncMock(return_value=always_resp)
    with patch("litellm.acompletion", new=mock_acompletion):
        result = await executor.execute(
            _llm_agent(tools=("loop_tool",)),
            context={},
        )

    assert mock_acompletion.await_count == max_iters
    assert result.tool_calls is not None
    assert len(result.tool_calls) == max_iters


# ── ToolRegistry unit tests ───────────────────────────────────────────────────


def test_tool_registry_register_and_len():
    """ToolRegistry.register adds an entry; __len__ reflects count."""
    r = ToolRegistry()
    assert len(r) == 0
    r.register("a", "desc", {"type": "object"}, lambda: None)
    assert len(r) == 1
    assert "a" in r


def test_tool_registry_specs_for_returns_openai_format():
    """specs_for returns a list of OpenAI-format tool dicts."""
    r = ToolRegistry()
    r.register(
        "my_tool",
        "My description",
        {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        lambda n: n,
    )
    specs = r.specs_for(("my_tool",))
    assert len(specs) == 1
    spec = specs[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "my_tool"
    assert spec["function"]["description"] == "My description"
    assert "n" in spec["function"]["parameters"]["properties"]


def test_tool_registry_specs_for_unknown_raises():
    """specs_for raises KeyError for an unregistered name."""
    r = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        r.specs_for(("nope",))


@pytest.mark.asyncio
async def test_tool_registry_execute_async():
    """execute calls an async callable and returns its result."""
    r = ToolRegistry()
    r.register("greet", "Greet.", {"type": "object"}, lambda name: f"hello {name}")
    result = await r.registry_execute_test("greet", {"name": "world"}) if False else await r.execute("greet", {"name": "world"})
    assert result == "hello world"


@pytest.mark.asyncio
async def test_tool_registry_execute_unknown_raises():
    """execute raises KeyError for an unregistered name."""
    r = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        await r.execute("ghost", {})


@pytest.mark.asyncio
async def test_tool_registry_execute_async_callable():
    """execute correctly awaits an async callable."""
    r = ToolRegistry()

    async def async_fn(x: int) -> int:
        return x + 1

    r.register("inc", "Increment.", {"type": "object"}, async_fn)
    result = await r.execute("inc", {"x": 5})
    assert result == 6
