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
"""Example 17 — Fine-tune domain models with Unsloth + Sagewai agents.

Demonstrates the complete fine-tuning pipeline: use Sagewai agents to
collect high-quality training data, export it for Unsloth fine-tuning,
and then discover and route to the resulting local model via the harness.

**Pipeline**:

1. Agent generates domain Q&A pairs from seed topics
2. Data is exported in Alpaca/ChatML format for Unsloth
3. Unsloth CLI fine-tunes the base model (shown as shell commands)
4. Harness auto-discovers the served model via local LLM discovery
5. Requests are routed to the fine-tuned model at $0/token

Requirements::

    pip install sagewai[harness]

Usage::

    python 17_unsloth_finetune.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from sagewai.engines.universal import UniversalAgent
from sagewai.harness.discovery import discover_local_backends
from sagewai.harness.models import ModelTierConfig
from sagewai.observability.costs import CostTracker


async def collect_training_data(agent: UniversalAgent, topics: list[str]) -> list[dict]:
    """Use an agent to generate Q&A pairs for fine-tuning."""
    pairs: list[dict] = []

    for topic in topics:
        prompt = (
            f"Generate 3 high-quality Q&A pairs about '{topic}' suitable for "
            f"training a domain-specific LLM. Return valid JSON: "
            f'[{{"instruction": "...", "input": "", "output": "..."}}]'
        )
        response = await agent.chat(prompt)

        try:
            data = json.loads(response)
            pairs.extend(data)
            print(f"  Collected {len(data)} pairs for: {topic}")
        except (json.JSONDecodeError, TypeError):
            print(f"  Skipped {topic} (non-JSON response)")

    return pairs


def export_alpaca_format(pairs: list[dict], output_path: Path) -> None:
    """Export training data in Alpaca format for Unsloth."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(pairs, f, indent=2)
    print(f"  Exported {len(pairs)} pairs to {output_path}")


async def main() -> None:
    """Run the Unsloth fine-tuning pipeline."""
    print("=" * 60)
    print("  Sagewai + Unsloth Fine-Tuning Pipeline")
    print("=" * 60)
    print()

    # -- Step 1: Collect training data with a Sagewai agent ----------
    print("Step 1: Collecting training data...")
    tracker = CostTracker()

    agent = UniversalAgent(
        name="data-collector",
        model=os.getenv("SAGEWAI_MODEL", "gpt-4o-mini"),
        system_prompt=(
            "You are a training data generator. Always respond with valid "
            "JSON arrays of instruction/input/output objects."
        ),
    )
    agent.on_event(tracker.event_hook)

    topics = ["customer support best practices", "refund policy handling"]
    pairs = await collect_training_data(agent, topics)
    print(f"  Total pairs collected: {len(pairs)}")
    print()

    # -- Step 2: Export for Unsloth ----------------------------------
    print("Step 2: Exporting training data...")
    output_path = Path("training_data/domain_alpaca.json")
    export_alpaca_format(pairs, output_path)
    print()

    # -- Step 3: Fine-tune with Unsloth (conceptual) -----------------
    print("Step 3: Fine-tuning with Unsloth (run these commands):")
    print()
    print("  # Install Unsloth")
    print("  pip install unsloth")
    print()
    print("  # Fine-tune Llama 3.1 8B on your data")
    print("  unsloth train \\")
    print("    --model unsloth/Llama-3.1-8B \\")
    print("    --data training_data/domain_alpaca.json \\")
    print("    --output ./my-domain-model \\")
    print("    --epochs 3 --lr 2e-5")
    print()
    print("  # Serve via llama-server (Unsloth's built-in server)")
    print("  unsloth serve ./my-domain-model --port 8001")
    print()

    # -- Step 4: Discover the local model ----------------------------
    print("Step 4: Discovering local LLM servers...")
    discovered = await discover_local_backends()

    if discovered:
        for name, server in discovered.items():
            print(f"  Found {name}: {', '.join(server.models[:3])}")
            print(f"    URL: {server.openai_compat_url}")
    else:
        print("  No local servers found (start Ollama or Unsloth first)")
    print()

    # -- Step 5: Configure harness routing ---------------------------
    print("Step 5: Harness routing configuration:")
    local_model = "my-domain-model"
    if discovered and "unsloth" in discovered:
        local_model = discovered["unsloth"].models[0]

    tier_config = ModelTierConfig(
        simple=f"openai/{local_model}",  # Fine-tuned model: $0/token
        medium="claude-sonnet-4-5-20250929",
        complex="claude-opus-4-6",
    )
    print(f"  SIMPLE  -> {tier_config.simple} (local, $0/token)")
    print(f"  MEDIUM  -> {tier_config.medium}")
    print(f"  COMPLEX -> {tier_config.complex}")
    print()

    # -- Summary -----------------------------------------------------
    print("=" * 60)
    print("  Pipeline Summary")
    print("=" * 60)
    print(f"  Training pairs generated: {len(pairs)}")
    print(f"  Data collection cost:     ${tracker.total_cost:.4f}")
    print(f"  Inference cost after:     $0.00/token (local model)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
