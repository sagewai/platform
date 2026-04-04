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
"""Example 09 — Govern your Claude Code costs in 5 minutes.

Minimal setup to proxy Claude Code through Sagewai's LLM Harness.
Every request is classified by complexity and routed to the cheapest
model that can handle it. A daily budget cap prevents runaway spend.

After starting this proxy, point Claude Code at it::

    export ANTHROPIC_BASE_URL=http://localhost:8100/v1
    export ANTHROPIC_API_KEY=<printed-key>

Requirements::

    pip install sagewai[harness] uvicorn

Usage::

    python 09_proxy_claude_code.py
"""

from __future__ import annotations

import asyncio
import os

from sagewai.harness.app import create_harness_app
from sagewai.harness.models import HarnessKey
from sagewai.harness.store import InMemoryHarnessStore


async def seed_key(store: InMemoryHarnessStore) -> str:
    """Create a single developer key with a $10/day budget."""
    key = HarnessKey(
        name="my-claude-code-key",
        user_id="developer",
        org_id="my-org",
        max_budget_daily_usd=10.00,
        max_budget_monthly_usd=100.00,
    )
    return await store.create_key(key)


def main() -> None:
    """Start the harness proxy for Claude Code."""
    print("=" * 55)
    print("  Sagewai Harness — Claude Code Proxy (5-min setup)")
    print("=" * 55)
    print()

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("WARNING: Set ANTHROPIC_API_KEY to forward real LLM calls.")
        print()

    app = create_harness_app(anthropic_api_key=anthropic_key)
    store = app.state.harness_store

    api_key = asyncio.run(seed_key(store))
    port = 8100

    print("Configure Claude Code:")
    print(f"  export ANTHROPIC_BASE_URL=http://localhost:{port}/v1")
    print(f"  export ANTHROPIC_API_KEY={api_key}")
    print()
    print("What happens:")
    print("  'fix this typo'        -> Haiku   (cheap)")
    print("  'refactor auth module' -> Sonnet  (mid)")
    print("  'design microservices' -> Opus    (full)")
    print()
    print(f"Daily budget: $10.00 | Monthly: $100.00")
    print(f"Dashboard: http://localhost:{port}/api/v1/harness/spend")
    print()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
