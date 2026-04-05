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
"""Example 23 — LLM Harness: Enterprise Proxy for Claude Code & Cursor.

Demonstrates deploying the LLM Harness as a smart proxy that sits between
AI coding tools and LLM providers, routing requests to the optimal model
based on task complexity and enforcing enterprise budget policies.

**The Problem**: Developers using Claude Code with Opus burn through token
budgets on simple tasks (fixing typos, adding imports) that Haiku handles
perfectly. No governance layer exists to optimize this.

**The Solution**: The harness classifies every request's complexity and
routes it to the cheapest model that can handle it — transparently.

Setup for Claude Code::

    export ANTHROPIC_BASE_URL=http://localhost:8100/v1
    export ANTHROPIC_API_KEY=sk-harness-<your-key>

Setup for Cursor / Copilot::

    API Base URL: http://localhost:8100/v1/chat/completions
    API Key: sk-harness-<your-key>

Requirements::

    pip install sagewai[harness] uvicorn

Usage::

    python 23_harness_proxy.py
    # Then configure your IDE to point at http://localhost:8100
"""

from __future__ import annotations

import asyncio
import os

from sagewai.harness.app import create_harness_app
from sagewai.harness.models import (
    ComplexityTier,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
)
from sagewai.harness.store import InMemoryHarnessStore


async def setup_demo_data(store: InMemoryHarnessStore) -> str:
    """Create demo policies, keys, and budget — returns a usable API key."""

    # ── Step 1: Create API keys for developers ───────────────────────
    # Each developer gets a scoped key with budget limits.
    alice_key = HarnessKey(
        name="alice-dev-key",
        user_id="alice",
        org_id="acme-corp",
        team_id="engineering",
        project_id="frontend",
        max_budget_daily_usd=5.00,
        max_budget_monthly_usd=50.00,
    )
    alice_plaintext = await store.create_key(alice_key)
    print(f"  Created key for Alice: ...{alice_plaintext[-8:]}")

    bob_key = HarnessKey(
        name="bob-intern-key",
        user_id="bob",
        org_id="acme-corp",
        team_id="engineering",
        max_budget_daily_usd=1.00,
        max_budget_monthly_usd=10.00,
    )
    bob_plaintext = await store.create_key(bob_key)
    print(f"  Created key for Bob:   ...{bob_plaintext[-8:]}")

    # ── Step 2: Create routing policies ──────────────────────────────

    # Org-wide default: route by complexity, allow overrides
    await store.create_policy(PolicyRule(
        name="acme-default",
        description="Default routing for all Acme developers",
        scope=PolicyScope(org_id="acme-corp"),
        priority=0,
        allow_override=True,
    ))
    print("  Policy: acme-default (org-wide, complexity-based routing)")

    # Interns: cap at MEDIUM tier, never use Opus
    await store.create_policy(PolicyRule(
        name="intern-cap",
        description="Interns capped at Sonnet — no Opus access",
        scope=PolicyScope(org_id="acme-corp", user_id="bob"),
        priority=10,
        max_tier=ComplexityTier.MEDIUM,
        blocked_models=["claude-opus-4-6"],
        allow_override=False,
    ))
    print("  Policy: intern-cap (Bob capped at Sonnet, no override)")

    # Senior devs: full access with override
    await store.create_policy(PolicyRule(
        name="senior-full-access",
        description="Senior engineers get full model access",
        scope=PolicyScope(org_id="acme-corp", user_id="alice"),
        priority=10,
        allow_override=True,
    ))
    print("  Policy: senior-full-access (Alice can use any model)")

    return alice_plaintext


def main() -> None:
    """Start the harness proxy server."""
    print("=" * 60)
    print("  LLM Harness — Enterprise Proxy for AI Coding Tools")
    print("=" * 60)
    print()

    # ── Configure tier mapping ───────────────────────────────────────
    # Which model to use for each complexity tier.
    tier_config = ModelTierConfig(
        simple="claude-haiku-4-5-20251001",   # Typos, imports, renames
        medium="claude-sonnet-4-5-20250929",  # Code generation, refactoring
        complex="claude-opus-4-6",            # Architecture, planning
    )
    print("Tier configuration:")
    print(f"  SIMPLE  → {tier_config.simple}")
    print(f"  MEDIUM  → {tier_config.medium}")
    print(f"  COMPLEX → {tier_config.complex}")
    print()

    # ── Create the app ───────────────────────────────────────────────
    # The harness needs at least one backend API key to forward calls.
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if not anthropic_key and not openai_key:
        print("WARNING: No ANTHROPIC_API_KEY or OPENAI_API_KEY found.")
        print("The proxy will start but cannot forward real LLM calls.")
        print("Set at least one API key to enable forwarding.")
        print()

    app = create_harness_app(
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
    )

    # Access the internal store to seed demo data
    store = app.state.harness_store

    # ── Seed demo data ───────────────────────────────────────────────
    print("Setting up demo data:")
    api_key = asyncio.run(setup_demo_data(store))
    print()

    # ── Print connection instructions ────────────────────────────────
    port = 8100
    print("=" * 60)
    print("  READY — Configure your tools:")
    print("=" * 60)
    print()
    print("  Claude Code:")
    print(f"    export ANTHROPIC_BASE_URL=http://localhost:{port}/v1")
    print(f"    export ANTHROPIC_API_KEY={api_key}")
    print()
    print("  Cursor / Continue / Copilot (OpenAI-compatible):")
    print(f"    API Base URL: http://localhost:{port}/v1")
    print(f"    API Key: {api_key}")
    print()
    print("  Admin dashboard:")
    print(f"    http://localhost:{port}/api/v1/harness/spend")
    print(f"    http://localhost:{port}/api/v1/harness/policies")
    print()
    print("  Dry-run classification test:")
    print(f"    curl -X POST http://localhost:{port}/api/v1/harness/test-classify \\")
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"messages": [{"role": "user", "content": "fix typo"}]}\'')
    print()
    print("  What happens when you use Claude Code:")
    print("    • 'fix this typo'          → Haiku  ($0.80/M input)")
    print("    • 'refactor auth module'   → Sonnet ($3.00/M input)")
    print("    • 'design microservices'   → Opus   ($15.00/M input)")
    print()
    print("  Transparency: check X-Harness-Model-Used response header")
    print()

    # ── Start the server ─────────────────────────────────────────────
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
