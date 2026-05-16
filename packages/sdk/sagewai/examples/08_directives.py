#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 08 — Directives: Directive-Powered Prompts — @context, @memory, @agent.

Directives are a prompt preprocessing layer that resolves special sigils
(``@context``, ``@memory``, ``@agent``) into rich context *before* the
LLM sees the prompt. This lets even small local models leverage the
full Sagewai infrastructure.

**The Problem**: Local models like Llama or Mistral can't call tools
natively. You end up writing custom RAG pipelines and glue code for
every model.

**The Solution**: Enable ``directives=True`` on any agent. The Directive
Engine intercepts prompts, resolves sigils into inline context, and
sends the enriched prompt to the LLM — no tool-calling required.

Requirements::

    pip install sagewai
    export OPENAI_API_KEY=sk-...

Usage::

    python 08_directives.py
"""

from __future__ import annotations

import asyncio

from sagewai import DirectiveEngine, UniversalAgent
from sagewai.memory.graph import GraphMemory


async def main() -> None:
    """Demonstrate directive-powered prompts."""

    # ── Set up memory with some facts ───────────────────────────────
    memory = GraphMemory()
    await memory.store("Sagewai is a Python SDK for building enterprise agentic platforms.")
    await memory.store("MCP is a protocol for tool interoperability.")
    await memory.add_relation("Sagewai", "supports", "MCP")

    # ── Option 1: Agent with directives=True (automatic) ────────────
    # When directives=True, the agent auto-creates a DirectiveEngine
    # and preprocesses every prompt before sending it to the LLM.
    agent = UniversalAgent(
        name="directive-bot",
        model="gpt-4o",
        system_prompt="You are a knowledgeable assistant.",
        memory=memory,
        directives=True,
    )

    print("Option 1: Agent with directives=True")
    print("  Prompt: 'Based on @memory, what is Sagewai?'")
    response = await agent.chat("Based on @memory, what is Sagewai?")
    print(f"  Agent: {response[:200]}")
    print()

    # ── Option 2: DirectiveEngine for manual preprocessing ──────────
    # For more control, use DirectiveEngine directly.
    engine = DirectiveEngine(model="gpt-4o")

    prompt = "Today is @datetime. Tell me about the current time."
    result = await engine.resolve(prompt)
    print("Option 2: Manual directive resolution")
    print(f"  Input:    {prompt}")
    print(f"  Resolved: {result.text[:200]}")
    print()

    # ── Directive syntax reference ──────────────────────────────────
    print("Directive Syntax Reference:")
    print("  @context('query')       — Search the Context Engine")
    print("  @context('q', scope='org') — Scoped context search")
    print("  @context('q', tags=['api']) — Tag-filtered context")
    print("  @memory                 — Inject memory context")
    print("  @agent:name('task')     — Delegate to another agent")
    print("  @wf:name('input')      — Invoke a saved workflow")
    print("  @datetime, @date, @time — Dynamic timestamps")
    print("  @user, @project         — Current user/project")
    print("  /tool.name('args')     — Call a registered tool")
    print("  /mcp.server.tool('a')  — Call an MCP tool")
    print("  #model:value            — Set model metadata")
    print("  {{ template }}          — Jinja2-style templates")


if __name__ == "__main__":
    asyncio.run(main())
