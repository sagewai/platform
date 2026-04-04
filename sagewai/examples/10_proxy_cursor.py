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
"""Example 10 — Proxy Cursor through Sagewai — budget + routing.

Sets up the LLM Harness as an OpenAI-compatible proxy for Cursor IDE.
Cursor sends requests to the standard ``/v1/chat/completions`` endpoint,
so the harness intercepts them transparently.

In Cursor Settings > Models > OpenAI API Key::

    API Key:      <printed-key>
    Base URL:     http://localhost:8100/v1

Requirements::

    pip install sagewai[harness] uvicorn

Usage::

    python 10_proxy_cursor.py
"""

from __future__ import annotations

import asyncio
import os

from sagewai.harness.app import create_harness_app
from sagewai.harness.models import HarnessKey, PolicyRule, PolicyScope
from sagewai.harness.store import InMemoryHarnessStore


async def seed_data(store: InMemoryHarnessStore) -> str:
    """Create a Cursor developer key and a routing policy."""
    key = HarnessKey(
        name="cursor-dev-key",
        user_id="cursor-user",
        org_id="my-org",
        max_budget_daily_usd=8.00,
        max_budget_monthly_usd=80.00,
    )
    plaintext = await store.create_key(key)

    # Org-wide policy: route by complexity, allow user override
    await store.create_policy(PolicyRule(
        name="cursor-routing",
        description="Complexity-based routing for Cursor requests",
        scope=PolicyScope(org_id="my-org"),
        priority=0,
        allow_override=True,
    ))

    return plaintext


def main() -> None:
    """Start the harness proxy for Cursor IDE."""
    print("=" * 55)
    print("  Sagewai Harness — Cursor IDE Proxy")
    print("=" * 55)
    print()

    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not openai_key and not anthropic_key:
        print("WARNING: Set OPENAI_API_KEY or ANTHROPIC_API_KEY to forward calls.")
        print()

    app = create_harness_app(
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
    )
    store = app.state.harness_store

    api_key = asyncio.run(seed_data(store))
    port = 8100

    print("Configure Cursor (Settings > Models > OpenAI API Key):")
    print(f"  API Key:   {api_key}")
    print(f"  Base URL:  http://localhost:{port}/v1")
    print()
    print("Cursor uses the OpenAI chat completions format.")
    print("The harness intercepts /v1/chat/completions and routes")
    print("each request to the optimal model by complexity tier.")
    print()
    print(f"Daily budget: $8.00 | Monthly: $80.00")
    print(f"Spend dashboard: http://localhost:{port}/api/v1/harness/spend")
    print()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
