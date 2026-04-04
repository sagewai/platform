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
"""Example 01 — Your First Sagewai Agent in 5 Lines.

The simplest possible agent. Create it, ask it something, get a response.

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY
    python 01_hello_agent.py
"""

from __future__ import annotations

import asyncio

from sagewai.engines.universal import UniversalAgent


async def main() -> None:
    agent = UniversalAgent(name="hello", model="gpt-4o-mini")
    response = await agent.chat("What are the 5 pillars of Sagewai?")
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
