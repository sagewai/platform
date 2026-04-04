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
"""Example 18 — Route simple->local, complex->cloud — $0/token for simple tasks.

Demonstrates the harness routing simple tasks to a local model (Ollama,
Unsloth, vLLM) at zero cost while sending complex tasks to cloud models.
Auto-discovers local servers and calculates cost savings.

**Cost savings example** (1000 requests/day):

- 70% simple tasks: local model at $0/token = $0.00
- 20% medium tasks: Sonnet at $3/M tokens = ~$0.60
- 10% complex tasks: Opus at $15/M tokens = ~$1.50
- Total: $2.10/day vs $15.00/day (all-Opus) = 86% savings

Requirements::

    pip install sagewai[harness]
    # Plus a local server: ollama serve, or vllm serve, etc.

Usage::

    python 18_local_llm_routing.py
"""

from __future__ import annotations

import asyncio
import os

from sagewai.harness.discovery import discover_local_backends
from sagewai.harness.models import (
    ComplexityTier,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
)
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.observability.costs import calculate_cost, get_model_pricing


def print_cost_comparison(tier_config: ModelTierConfig) -> None:
    """Show projected cost savings with local routing."""
    print("Cost Comparison (1000 requests/day, ~500 tokens each):")
    print()

    tokens_per_req = 500
    daily_requests = 1000
    distribution = {"simple": 0.70, "medium": 0.20, "complex": 0.10}

    # All-cloud baseline (everything uses Opus)
    baseline_daily = daily_requests * calculate_cost(
        tokens_per_req, tokens_per_req, "claude-opus-4-6",
    )

    # Tiered routing cost
    tiered_daily = 0.0
    for tier_name, fraction in distribution.items():
        model = getattr(tier_config, tier_name)
        req_count = daily_requests * fraction
        cost = req_count * calculate_cost(tokens_per_req, tokens_per_req, model)
        tiered_daily += cost
        price_in, price_out = get_model_pricing(model)
        label = f"${price_in:.2f}/M" if price_in > 0 else "FREE"
        print(f"  {tier_name.upper():8s}: {int(req_count):4d} reqs -> {model:40s} ({label})")

    print()
    print(f"  All-Opus baseline: ${baseline_daily:8.2f}/day  (${baseline_daily * 30:8.2f}/month)")
    print(f"  Tiered routing:    ${tiered_daily:8.2f}/day  (${tiered_daily * 30:8.2f}/month)")
    savings_pct = (1 - tiered_daily / baseline_daily) * 100 if baseline_daily > 0 else 0
    print(f"  Savings:           {savings_pct:.0f}%")


async def main() -> None:
    """Configure and demonstrate local LLM routing."""
    print("=" * 60)
    print("  Local LLM Routing — Zero-Cost Simple Tasks")
    print("=" * 60)
    print()

    # -- Step 1: Discover local LLM servers --------------------------
    print("Scanning for local LLM servers...")
    discovered = await discover_local_backends()

    local_model = "ollama/llama3.1:8b"  # Default fallback
    if discovered:
        for name, server in discovered.items():
            models_str = ", ".join(server.models[:3])
            if len(server.models) > 3:
                models_str += f" (+{len(server.models) - 3} more)"
            print(f"  Found {name}: {models_str}")
            print(f"    Base URL: {server.openai_compat_url}")
            # Use first discovered model for simple tier
            if local_model == "ollama/llama3.1:8b":
                prefix = f"{name}/" if name != "ollama" else "openai/"
                local_model = f"{prefix}{server.models[0]}"
    else:
        print("  No local servers found.")
        print("  To enable local routing, start one of:")
        print("    ollama serve              (port 11434)")
        print("    vllm serve <model>        (port 8000)")
        print("    unsloth serve <model>     (port 8001)")
    print()

    # -- Step 2: Configure tier mapping ------------------------------
    tier_config = ModelTierConfig(
        simple=local_model,
        medium="claude-sonnet-4-5-20250929",
        complex="claude-opus-4-6",
    )
    print("Tier Configuration:")
    print(f"  SIMPLE  -> {tier_config.simple} ($0/token)")
    print(f"  MEDIUM  -> {tier_config.medium}")
    print(f"  COMPLEX -> {tier_config.complex}")
    print()

    # -- Step 3: Show cost savings -----------------------------------
    print_cost_comparison(tier_config)
    print()

    # -- Step 4: Set up policies -------------------------------------
    print("Setting up routing policies...")
    store = InMemoryHarnessStore()

    # Create a developer key
    dev_key = HarnessKey(
        name="dev-team-key",
        user_id="developer",
        org_id="my-org",
        max_budget_daily_usd=10.00,
    )
    plaintext = await store.create_key(dev_key)
    print(f"  API key created: ...{plaintext[-8:]}")

    # Policy: default complexity-based routing
    await store.create_policy(PolicyRule(
        name="local-first",
        description="Route simple tasks to local model, save on costs",
        scope=PolicyScope(org_id="my-org"),
        priority=0,
        allow_override=True,
    ))
    print("  Policy: local-first (simple->local, medium->sonnet, complex->opus)")

    # Policy: force local for CI/testing
    await store.create_policy(PolicyRule(
        name="ci-local-only",
        description="CI environment uses only local models",
        scope=PolicyScope(org_id="my-org", team_id="ci"),
        priority=10,
        force_model=local_model,
        allow_override=False,
    ))
    print("  Policy: ci-local-only (all CI traffic -> local, $0)")
    print()

    # -- What each request type routes to ----------------------------
    print("Routing Examples:")
    examples = [
        ("fix typo in README", ComplexityTier.SIMPLE),
        ("add input validation to login form", ComplexityTier.MEDIUM),
        ("design microservices architecture", ComplexityTier.COMPLEX),
    ]
    for prompt, tier in examples:
        model = tier_config.model_for_tier(tier)
        price_in, _ = get_model_pricing(model)
        label = "FREE" if price_in == 0 else f"${price_in}/M input"
        print(f"  '{prompt}'")
        print(f"    -> {tier.value.upper()} -> {model} ({label})")
    print()


if __name__ == "__main__":
    asyncio.run(main())
