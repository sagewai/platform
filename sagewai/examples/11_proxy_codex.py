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
"""Example 11 — Proxy Codex CLI through Sagewai.

Routes OpenAI Codex CLI requests through the LLM Harness for
cost governance and smart model routing. Codex uses the OpenAI
API format, so point its environment variables at the harness::

    export OPENAI_BASE_URL=http://localhost:8100/v1
    export OPENAI_API_KEY=<printed-key>

Requirements::

    pip install sagewai[harness] uvicorn

Usage::

    python 11_proxy_codex.py
"""

from __future__ import annotations

import asyncio
import os

from sagewai.harness.app import create_harness_app
from sagewai.harness.models import HarnessKey
from sagewai.harness.store import InMemoryHarnessStore


async def seed_key(store: InMemoryHarnessStore) -> str:
    """Create a Codex developer key with budget limits."""
    key = HarnessKey(
        name="codex-cli-key",
        user_id="codex-user",
        org_id="my-org",
        max_budget_daily_usd=15.00,
        max_budget_monthly_usd=150.00,
    )
    return await store.create_key(key)


def main() -> None:
    """Start the harness proxy for OpenAI Codex CLI."""
    print("=" * 55)
    print("  Sagewai Harness — OpenAI Codex CLI Proxy")
    print("=" * 55)
    print()

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print("WARNING: Set OPENAI_API_KEY to forward real LLM calls.")
        print()

    app = create_harness_app(openai_api_key=openai_key)
    store = app.state.harness_store

    api_key = asyncio.run(seed_key(store))
    port = 8100

    print("Configure Codex CLI:")
    print(f"  export OPENAI_BASE_URL=http://localhost:{port}/v1")
    print(f"  export OPENAI_API_KEY={api_key}")
    print()
    print("How it works:")
    print("  Codex sends OpenAI-format requests to the harness.")
    print("  The harness classifies complexity and routes to the")
    print("  cheapest capable model, then forwards to OpenAI.")
    print()
    print(f"Daily budget: $15.00 | Monthly: $150.00")
    print(f"Spend dashboard: http://localhost:{port}/api/v1/harness/spend")
    print()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
