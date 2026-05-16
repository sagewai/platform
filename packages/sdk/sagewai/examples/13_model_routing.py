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
"""Example 13 — Smart routing — right model for every task.

Demonstrates the request classifier and model routing engine.
Each prompt is scored for complexity and mapped to a tier:

- **SIMPLE** -> Haiku  (typos, imports, one-liners)
- **MEDIUM** -> Sonnet (refactoring, code generation)
- **COMPLEX** -> Opus  (architecture, multi-file design)

No LLM calls are made during classification — it uses pure
heuristic scoring (keyword detection, token count, code blocks).

Requirements::

    pip install sagewai[harness]

Usage::

    python 13_model_routing.py
"""

from __future__ import annotations

import asyncio

from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.models import ModelTierConfig


async def main() -> None:
    """Classify example prompts and show routing decisions."""
    print("=" * 55)
    print("  LLM Harness — Smart Model Routing Demo")
    print("=" * 55)
    print()

    classifier = RequestClassifier()
    tier_config = ModelTierConfig(
        simple="claude-haiku-4-5-20251001",
        medium="claude-sonnet-4-5-20250929",
        complex="claude-opus-4-6",
    )

    # ── Example prompts with expected tiers ──────────────────────
    prompts = [
        ("fix the typo in line 42", "SIMPLE"),
        ("add the missing import for os.path", "SIMPLE"),
        ("rename getUserName to get_user_name", "SIMPLE"),
        ("refactor the authentication module to use JWT tokens", "MEDIUM"),
        ("write a function that parses CSV files with error handling", "MEDIUM"),
        ("implement a retry mechanism with exponential backoff", "MEDIUM"),
        (
            "design a microservices architecture for our payment system "
            "with event sourcing, CQRS, and distributed tracing across "
            "multiple services including auth, billing, and notifications",
            "COMPLEX",
        ),
        (
            "review and restructure the entire data pipeline to handle "
            "10x throughput with multi-region failover and end-to-end "
            "encryption, including migration plan for existing data",
            "COMPLEX",
        ),
    ]

    print(f"{'Prompt':<55} {'Tier':<10} {'Model':<30} {'Score'}")
    print("-" * 105)

    for prompt, expected in prompts:
        messages = [{"role": "user", "content": prompt}]
        result = classifier.classify(messages)
        model = tier_config.model_for_tier(result.tier)
        short = prompt[:52] + "..." if len(prompt) > 55 else prompt

        print(f"{short:<55} {result.tier.value:<10} {model:<30} {result.score}")

    print()
    print("The classifier uses keyword detection, token count,")
    print("and structural signals (code blocks, conversation depth)")
    print("to route each request — zero LLM calls needed.")


if __name__ == "__main__":
    asyncio.run(main())
