#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 24 — LLM Harness: SDK-Level Smart Routing for Agents.

Demonstrates using the HarnessingAgent to control LLM model selection
across multiple agents — no external proxy needed.

**The Problem**: You're building an AI application with multiple agents.
Some handle simple tasks (formatting, lookups) while others do complex
reasoning (planning, architecture). All default to the same expensive model.

**The Solution**: The HarnessingAgent intercepts every ``_call_llm()``
call, classifies the request complexity, and routes to the right model.

This example shows three approaches:

1. **HarnessingAgent** — supervisor that wraps multiple child agents
2. **harness_wrap()** — patch a single existing agent
3. **HarnessMiddleware** — mixin for custom agent classes

Requirements::

    pip install sagewai

Usage::

    python 24_harness_agent.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from sagewai.harness.agent import HarnessingAgent, register_harness_directives
from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.middleware import HarnessMiddleware, harness_wrap
from sagewai.harness.models import ModelTierConfig


# ── Mock agent (avoids real LLM calls for the example) ───────────────


@dataclass
class MockConfig:
    name: str = "mock"
    model: str = "claude-opus-4-6"


@dataclass
class MockUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    model: str = ""
    duration_ms: float = 0.0


@dataclass
class MockResponse:
    role: str = "assistant"
    content: str = "Done!"
    tool_calls: list[Any] = field(default_factory=list)
    usage: MockUsage | None = None


class MockAgent:
    """Simulates a BaseAgent for demonstration purposes."""

    def __init__(self, name: str = "mock", model: str = "claude-opus-4-6"):
        self.config = MockConfig(name=name, model=model)
        self._current_model_override: str | None = None
        self._rate_limiter = None

    async def _call_llm(self, messages: Any, tools: Any) -> MockResponse:
        model = self._current_model_override or self.config.model
        # Simulate LLM response
        await asyncio.sleep(0.01)
        return MockResponse(
            content=f"[{model}] Response to your request.",
            usage=MockUsage(model=model),
        )

    async def chat(self, message: str) -> str:
        msgs = [MockResponse(role="user", content=message)]
        result = await self._call_llm(msgs, [])
        return result.content


class HarnessedMockAgent(HarnessMiddleware, MockAgent):
    """MockAgent with HarnessMiddleware for approach #3."""

    pass


# ── Demo scenarios ───────────────────────────────────────────────────


async def demo_harnessing_agent() -> None:
    """Approach 1: HarnessingAgent supervisor."""
    print("=" * 60)
    print("  Approach 1: HarnessingAgent (Supervisor)")
    print("=" * 60)
    print()

    # Create child agents — all default to expensive Opus
    planner = MockAgent(name="planner", model="claude-opus-4-6")
    coder = MockAgent(name="coder", model="claude-opus-4-6")
    reviewer = MockAgent(name="reviewer", model="claude-opus-4-6")

    # Wrap them with a harnessing agent
    tier_config = ModelTierConfig(
        simple="claude-haiku-4-5-20251001",
        medium="claude-sonnet-4-5-20250929",
        complex="claude-opus-4-6",
    )
    harness = HarnessingAgent(
        name="cost-optimizer",
        agents=[planner, coder, reviewer],
        tier_config=tier_config,
    )
    print(f"  Supervisor managing {len(harness.agents)} agents")
    print()

    # Simulate different request types
    requests = [
        ("planner", "fix typo in README", planner),
        ("coder", "add import os at the top", coder),
        ("planner", (
            "Design a complete microservices architecture for the "
            "authentication system with security audit and review"
        ), planner),
        ("reviewer", "rename variable x to user_count", reviewer),
        ("coder", (
            "Implement a caching layer with Redis that handles "
            "cache invalidation, TTL management, and fallback strategies"
        ), coder),
    ]

    for agent_name, message, agent in requests:
        result = await agent.chat(message)
        # Check what the harness routed to
        log = agent._harness_log[-1] if agent._harness_log else {}
        tier = log.get("tier", "?")
        target = log.get("target_model", "?")
        overridden = log.get("overridden", False)
        arrow = "→" if overridden else "="

        short_msg = message[:45] + "..." if len(message) > 45 else message
        print(f"  [{agent_name}] \"{short_msg}\"")
        print(f"           tier={tier} {arrow} {target}")
        print()

    # Show aggregate stats
    stats = harness.get_stats()
    print("  ── Aggregate Stats ──")
    print(f"  Total calls:     {stats['total_calls']}")
    print(f"  Total overrides: {stats['total_overrides']}")
    print(f"  Override rate:   {stats['override_rate']:.0%}")
    print(f"  Savings calls:   {stats['savings_calls']} "
          f"(requests routed to cheaper models)")
    print(f"  Tier distribution: {stats['tier_distribution']}")
    print()


async def demo_harness_wrap() -> None:
    """Approach 2: harness_wrap() for a single agent."""
    print("=" * 60)
    print("  Approach 2: harness_wrap() (Single Agent)")
    print("=" * 60)
    print()

    # Create an agent that defaults to Opus
    agent = MockAgent(name="my-agent", model="claude-opus-4-6")
    print(f"  Before: model={agent.config.model}")

    # One line to add smart routing
    harness_wrap(
        agent,
        tier_config=ModelTierConfig(
            simple="claude-haiku-4-5-20251001",
            medium="claude-sonnet-4-5-20250929",
            complex="claude-opus-4-6",
        ),
    )
    print("  Applied: harness_wrap(agent, tier_config=...)")
    print()

    # Simple request → Haiku
    await agent.chat("fix typo")
    log = agent._harness_log[-1]
    print(f"  'fix typo' → {log['target_model']} (tier={log['tier']})")

    # Complex request → stays on Opus
    await agent.chat(
        "Design and implement the complete authentication system "
        "with security audit and end-to-end review"
    )
    log = agent._harness_log[-1]
    print(f"  'Design auth system...' → {log['target_model']} "
          f"(tier={log['tier']})")
    print()


async def demo_middleware_mixin() -> None:
    """Approach 3: HarnessMiddleware mixin for custom agents."""
    print("=" * 60)
    print("  Approach 3: HarnessMiddleware (Mixin)")
    print("=" * 60)
    print()

    # Use the mixin on a custom agent class
    agent = HarnessedMockAgent(name="custom", model="claude-opus-4-6")

    # Enable the harness
    agent.enable_harness(
        tier_config=ModelTierConfig(
            simple="gpt-4o-mini",    # Can use any provider's models!
            medium="gpt-4o",
            complex="claude-opus-4-6",
        ),
    )
    print("  Tier config: simple→gpt-4o-mini, medium→gpt-4o, complex→opus")
    print()

    # Cross-provider routing
    await agent.chat("add import")
    log = agent.harness_routing_log[-1]
    print(f"  'add import' → {log['target_model']} (score={log['score']})")

    await agent.chat("refactor the database module for better performance")
    log = agent.harness_routing_log[-1]
    print(f"  'refactor database...' → {log['target_model']} "
          f"(score={log['score']})")

    # Disable and re-enable
    agent.disable_harness()
    print("  Harness disabled — next call uses default model")
    await agent.chat("quick fix")
    print(f"  Used: {agent._current_model_override or agent.config.model}")

    agent.enable_harness()
    print("  Harness re-enabled")
    print()


async def demo_dynamic_config() -> None:
    """Show dynamic tier config updates."""
    print("=" * 60)
    print("  Dynamic Configuration")
    print("=" * 60)
    print()

    agent = MockAgent(name="dynamic", model="claude-opus-4-6")
    harness = HarnessingAgent(
        name="dynamic-harness",
        agents=[agent],
        tier_config=ModelTierConfig(
            simple="claude-haiku-4-5-20251001",
            medium="claude-sonnet-4-5-20250929",
            complex="claude-opus-4-6",
        ),
    )

    await agent.chat("fix typo")
    print(f"  Before update: simple→{agent._harness_log[-1]['target_model']}")

    # Switch to OpenAI models mid-session
    harness.update_tier_config(ModelTierConfig(
        simple="gpt-4o-mini",
        medium="gpt-4o",
        complex="o3",
    ))
    print("  Updated tier config to OpenAI models")

    await agent.chat("fix another typo")
    print(f"  After update:  simple→{agent._harness_log[-1]['target_model']}")
    print()

    # Disable for all agents at once
    harness.set_enabled(False)
    print("  Harness disabled across all agents")
    harness.set_enabled(True)
    print("  Harness re-enabled")
    print()


async def main() -> None:
    """Run all demo scenarios."""
    print()
    print("  LLM Harness — SDK-Level Smart Routing")
    print("  No proxy needed. Routing happens inside BaseAgent._call_llm()")
    print()

    await demo_harnessing_agent()
    await demo_harness_wrap()
    await demo_middleware_mixin()
    await demo_dynamic_config()

    print("=" * 60)
    print("  Summary")
    print("=" * 60)
    print()
    print("  Three ways to use the harness:")
    print()
    print("  1. HarnessingAgent — supervisor for multiple agents")
    print("     harness = HarnessingAgent(agents=[a, b, c])")
    print()
    print("  2. harness_wrap() — patch any existing agent")
    print("     harness_wrap(agent, tier_config=ModelTierConfig(...))")
    print()
    print("  3. HarnessMiddleware — mixin for custom classes")
    print("     class MyAgent(HarnessMiddleware, UniversalAgent): ...")
    print()
    print("  All three approaches:")
    print("  • Classify request complexity (SIMPLE/MEDIUM/COMPLEX)")
    print("  • Route to the cheapest capable model automatically")
    print("  • Respect #model:name directive overrides (user > harness)")
    print("  • Log every routing decision for audit")
    print("  • Support cross-provider routing (Anthropic + OpenAI + etc)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
