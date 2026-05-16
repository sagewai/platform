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
"""Example 03 — One Agent, Many Models: Zero Lock-In.

Sagewai is model-agnostic. The same agent can run on any LLM provider.
Switch models by changing a single string — no code changes needed.

Requirements::

    pip install sagewai

Usage::

    # Set API keys for providers you want to test
    export OPENAI_API_KEY=sk-...
    export ANTHROPIC_API_KEY=sk-ant-...
    export GEMINI_API_KEY=...
    python 03_multi_model.py
"""

from __future__ import annotations

import asyncio
import os
import time

from sagewai.engines.universal import UniversalAgent

# Models to compare — comment out any you don't have keys for
MODELS = [
    ("gpt-4o-mini", "OPENAI_API_KEY"),
    ("claude-sonnet-4-5-20250929", "ANTHROPIC_API_KEY"),
    ("gemini/gemini-2.5-flash", "GEMINI_API_KEY"),
    ("ollama/llama3.2", None),  # Local — no key needed
]

TASK = "Explain what a binary search tree is in exactly 2 sentences."


async def main() -> None:
    print(f"Task: {TASK}")
    print("=" * 60)

    for model_name, env_key in MODELS:
        if env_key and not os.getenv(env_key):
            print(f"\n[{model_name}] Skipped — {env_key} not set")
            continue

        agent = UniversalAgent(name="comparator", model=model_name)
        start = time.perf_counter()
        try:
            response = await agent.chat(TASK)
            elapsed = time.perf_counter() - start
            print(f"\n[{model_name}] ({elapsed:.2f}s)")
            print(f"  {response[:200]}")
        except Exception as e:
            print(f"\n[{model_name}] Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
