#!/usr/bin/env python3
# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 02 — Give Your Agent Superpowers with Custom Tools.

Agents become useful when they can take actions. The @tool decorator
turns any Python function into a tool the agent can call autonomously.

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...
    python 02_tool_agent.py
"""

from __future__ import annotations

import asyncio
import math

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool


@tool
def calculate(expression: str) -> str:
    """Evaluate a math expression safely.

    Args:
        expression: A mathematical expression like '2 + 2' or 'sqrt(144)'.
    """
    allowed = {
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "pi": math.pi, "e": math.e, "abs": abs, "round": round,
    }
    result = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
    return str(result)


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city (demo — returns mock data).

    Args:
        city: Name of the city.
    """
    weather_data = {
        "berlin": "14C, partly cloudy",
        "tokyo": "22C, sunny",
        "new york": "18C, light rain",
    }
    return weather_data.get(city.lower(), f"Weather data not available for {city}")


async def main() -> None:
    agent = UniversalAgent(
        name="assistant",
        model="gpt-4o-mini",
        tools=[calculate, get_weather],
        system_prompt="You are a helpful assistant with access to tools.",
    )

    # The agent will automatically decide to use the tools
    response = await agent.chat(
        "What's the square root of 144? Also, what's the weather in Berlin?"
    )
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
