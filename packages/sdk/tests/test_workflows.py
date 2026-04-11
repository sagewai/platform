"""Tests for workflow agent patterns (Sequential, Parallel, Loop)."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.workflows import LoopAgent, ParallelAgent, SequentialAgent


class EchoAgent(BaseAgent):
    """Test agent that echoes input with a prefix."""

    def __init__(self, prefix: str = "", **kwargs):
        super().__init__(**kwargs)
        self.prefix = prefix
        self.calls: list[str] = []

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.calls.append(message)
        return f"{self.prefix}{message}"


class CountingAgent(BaseAgent):
    """Test agent that tracks call count and returns incrementing results."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.call_count += 1
        return f"iteration-{self.call_count}: {message}"


# ------------------------------------------------------------------
# SequentialAgent tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_single_agent():
    """Sequential with one agent passes through correctly."""
    agent = EchoAgent(prefix="A:", name="a", model="mock")
    seq = SequentialAgent(name="seq", agents=[agent])

    result = await seq.chat("hello")
    assert result == "A:hello"
    assert agent.calls == ["hello"]


@pytest.mark.asyncio
async def test_sequential_pipes_output():
    """Sequential pipes output of each agent as input to the next."""
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")
    c = EchoAgent(prefix="C:", name="c", model="mock")
    seq = SequentialAgent(name="seq", agents=[a, b, c])

    result = await seq.chat("start")
    assert result == "C:B:A:start"
    assert a.calls == ["start"]
    assert b.calls == ["A:start"]
    assert c.calls == ["B:A:start"]


@pytest.mark.asyncio
async def test_sequential_preserves_order():
    """Sequential runs agents in the exact order given."""
    order: list[str] = []

    class OrderTracker(BaseAgent):
        def __init__(self, label: str, **kwargs):
            super().__init__(**kwargs)
            self.label = label

        async def _invoke_llm(self, messages, tools, *, model_override=None):
            raise NotImplementedError

        async def chat(self, message: str) -> str:
            order.append(self.label)
            return message

    agents = [OrderTracker(label=f"agent-{i}", name=f"a{i}", model="mock") for i in range(5)]
    seq = SequentialAgent(name="seq", agents=agents)
    await seq.chat("test")
    assert order == ["agent-0", "agent-1", "agent-2", "agent-3", "agent-4"]


def test_sequential_requires_agents():
    """SequentialAgent raises ValueError with empty agent list."""
    with pytest.raises(ValueError, match="at least one sub-agent"):
        SequentialAgent(name="empty", agents=[])


# ------------------------------------------------------------------
# ParallelAgent tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_runs_concurrently():
    """Parallel runs all agents and merges results."""
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")
    par = ParallelAgent(name="par", agents=[a, b])

    result = await par.chat("input")
    assert result == "A:input\n\nB:input"
    assert a.calls == ["input"]
    assert b.calls == ["input"]


@pytest.mark.asyncio
async def test_parallel_all_receive_same_input():
    """All parallel agents receive the same user input."""
    agents = [EchoAgent(prefix=f"{i}:", name=f"a{i}", model="mock") for i in range(4)]
    par = ParallelAgent(name="par", agents=agents)

    await par.chat("shared-input")
    for agent in agents:
        assert agent.calls == ["shared-input"]


@pytest.mark.asyncio
async def test_parallel_custom_merge():
    """ParallelAgent accepts a custom merge function."""
    a = EchoAgent(prefix="", name="a", model="mock")
    b = EchoAgent(prefix="", name="b", model="mock")

    def csv_merge(results: list[str]) -> str:
        return ",".join(results)

    par = ParallelAgent(name="par", agents=[a, b], merge=csv_merge)
    result = await par.chat("x")
    assert result == "x,x"


def test_parallel_requires_agents():
    """ParallelAgent raises ValueError with empty agent list."""
    with pytest.raises(ValueError, match="at least one sub-agent"):
        ParallelAgent(name="empty", agents=[])


# ------------------------------------------------------------------
# LoopAgent tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_runs_max_iterations():
    """LoopAgent runs max_iterations times when no stop condition."""
    agent = CountingAgent(name="counter", model="mock")
    loop = LoopAgent(name="loop", agent=agent, max_iterations=3)

    result = await loop.chat("go")
    assert agent.call_count == 3
    assert "iteration-3" in result


@pytest.mark.asyncio
async def test_loop_stops_on_condition():
    """LoopAgent stops early when should_stop returns True."""
    agent = CountingAgent(name="counter", model="mock")

    def stop_at_2(result: str, iteration: int) -> bool:
        return iteration >= 1  # Stop after 2nd iteration (0-indexed)

    loop = LoopAgent(name="loop", agent=agent, should_stop=stop_at_2, max_iterations=10)

    await loop.chat("go")
    assert agent.call_count == 2


@pytest.mark.asyncio
async def test_loop_feeds_output_back():
    """LoopAgent feeds each output as the next input."""
    calls: list[str] = []

    class AppendAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            raise NotImplementedError

        async def chat(self, message: str) -> str:
            calls.append(message)
            return message + "+"

    agent = AppendAgent(name="appender", model="mock")
    loop = LoopAgent(name="loop", agent=agent, max_iterations=3)

    result = await loop.chat("x")
    assert calls == ["x", "x+", "x++"]
    assert result == "x+++"


@pytest.mark.asyncio
async def test_loop_condition_receives_result():
    """should_stop receives the actual result string."""
    seen_results: list[str] = []

    def tracker(result: str, iteration: int) -> bool:
        seen_results.append(result)
        return iteration >= 2

    agent = CountingAgent(name="counter", model="mock")
    loop = LoopAgent(name="loop", agent=agent, should_stop=tracker, max_iterations=5)

    await loop.chat("input")
    assert len(seen_results) == 3
    assert all("iteration-" in r for r in seen_results)


# ------------------------------------------------------------------
# Composition tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nested_sequential_in_parallel():
    """Workflow agents can be nested: parallel of sequentials."""
    a1 = EchoAgent(prefix="X:", name="x", model="mock")
    a2 = EchoAgent(prefix="Y:", name="y", model="mock")
    seq1 = SequentialAgent(name="seq1", agents=[a1])
    seq2 = SequentialAgent(name="seq2", agents=[a2])

    par = ParallelAgent(name="nested", agents=[seq1, seq2])
    result = await par.chat("test")
    assert result == "X:test\n\nY:test"


@pytest.mark.asyncio
async def test_loop_with_sequential_subagent():
    """LoopAgent can wrap a SequentialAgent."""
    step1 = EchoAgent(prefix="[1]", name="s1", model="mock")
    step2 = EchoAgent(prefix="[2]", name="s2", model="mock")
    pipeline = SequentialAgent(name="pipe", agents=[step1, step2])

    loop = LoopAgent(name="loop", agent=pipeline, max_iterations=2)
    result = await loop.chat("start")
    # First iteration: [2][1]start → Second iteration: [2][1][2][1]start
    assert result == "[2][1][2][1]start"
