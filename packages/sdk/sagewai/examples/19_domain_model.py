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
"""Example 19 — Build domain-specific LLMs for your industry.

Demonstrates a domain model training pipeline for legal, medical, or
finance verticals. A Sagewai agent acts as a domain expert, generating
high-quality Q&A training pairs that can be fine-tuned with Unsloth
or any compatible training framework.

**Pipeline**:

1. Define domain taxonomy (topics, subtopics, difficulty levels)
2. Agent generates instruction-following training pairs
3. Quality filter removes low-confidence pairs
4. Export in multiple formats (Alpaca, ShareGPT, ChatML)
5. Fine-tune and serve via harness discovery

Requirements::

    pip install sagewai

Usage::

    python 19_domain_model.py
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field

from sagewai.engines.universal import UniversalAgent
from sagewai.observability.costs import CostTracker


@dataclass
class DomainTaxonomy:
    """Definition of a domain's topic structure."""

    name: str
    description: str
    topics: list[dict[str, str]] = field(default_factory=list)


# Pre-defined domain taxonomies
DOMAINS: dict[str, DomainTaxonomy] = {
    "legal": DomainTaxonomy(
        name="Legal",
        description="Contract law, compliance, and regulatory questions",
        topics=[
            {"topic": "contract review", "context": "SaaS agreements"},
            {"topic": "GDPR compliance", "context": "data processing"},
            {"topic": "employment law", "context": "termination procedures"},
        ],
    ),
    "medical": DomainTaxonomy(
        name="Medical",
        description="Clinical decision support and patient triage",
        topics=[
            {"topic": "symptom assessment", "context": "primary care"},
            {"topic": "drug interactions", "context": "pharmacy review"},
            {"topic": "lab result interpretation", "context": "routine panels"},
        ],
    ),
    "finance": DomainTaxonomy(
        name="Finance",
        description="Financial analysis, risk assessment, and compliance",
        topics=[
            {"topic": "financial ratios", "context": "quarterly reports"},
            {"topic": "risk scoring", "context": "credit applications"},
            {"topic": "regulatory reporting", "context": "SOX compliance"},
        ],
    ),
}


async def generate_domain_pairs(
    agent: UniversalAgent,
    taxonomy: DomainTaxonomy,
    pairs_per_topic: int = 3,
) -> list[dict]:
    """Generate domain-specific Q&A pairs using the agent."""
    all_pairs: list[dict] = []

    for topic_def in taxonomy.topics:
        topic = topic_def["topic"]
        context = topic_def["context"]

        prompt = (
            f"You are a {taxonomy.name} domain expert. Generate {pairs_per_topic} "
            f"training pairs about '{topic}' in the context of '{context}'. "
            f"Each pair should have varying difficulty. Return valid JSON:\n"
            f'[{{"instruction": "...", "input": "...", "output": "...", '
            f'"difficulty": "easy|medium|hard"}}]'
        )
        response = await agent.chat(prompt)

        try:
            data = json.loads(response)
            for item in data:
                item["domain"] = taxonomy.name.lower()
                item["topic"] = topic
            all_pairs.extend(data)
            print(f"    {topic}: {len(data)} pairs generated")
        except (json.JSONDecodeError, TypeError):
            print(f"    {topic}: skipped (parse error)")

    return all_pairs


def export_sharegpt_format(pairs: list[dict]) -> list[dict]:
    """Convert Alpaca pairs to ShareGPT multi-turn format."""
    conversations = []
    for pair in pairs:
        conv = {
            "conversations": [
                {"from": "human", "value": pair["instruction"]},
                {"from": "gpt", "value": pair["output"]},
            ],
            "domain": pair.get("domain", ""),
        }
        if pair.get("input"):
            conv["conversations"][0]["value"] += f"\n\nContext: {pair['input']}"
        conversations.append(conv)
    return conversations


async def main() -> None:
    """Run the domain model training pipeline."""
    print("=" * 60)
    print("  Domain-Specific LLM Training Pipeline")
    print("=" * 60)
    print()

    # Select domain (default: legal)
    domain_key = os.getenv("DOMAIN", "legal")
    taxonomy = DOMAINS.get(domain_key, DOMAINS["legal"])
    print(f"Domain: {taxonomy.name} — {taxonomy.description}")
    print(f"Topics: {len(taxonomy.topics)}")
    print()

    # ── Step 1: Generate training data ──────────────────────────────
    print("Step 1: Generating domain training data...")
    tracker = CostTracker()

    agent = UniversalAgent(
        name="domain-expert",
        model=os.getenv("SAGEWAI_MODEL", "gpt-4o-mini"),
        system_prompt=(
            f"You are an expert in {taxonomy.name.lower()}. Generate precise, "
            f"factual training data. Always respond with valid JSON arrays."
        ),
    )
    agent.on_event(tracker.event_hook)

    pairs = await generate_domain_pairs(agent, taxonomy)
    print(f"  Total pairs: {len(pairs)}")
    print()

    # ── Step 2: Quality filtering ───────────────────────────────────
    print("Step 2: Quality filtering...")
    filtered = [p for p in pairs if len(p.get("output", "")) > 50]
    removed = len(pairs) - len(filtered)
    print(f"  Kept: {len(filtered)}, removed: {removed} (too short)")
    print()

    # ── Step 3: Export formats ──────────────────────────────────────
    print("Step 3: Export formats available:")
    print(f"  Alpaca format:   {len(filtered)} pairs (instruction/input/output)")
    sharegpt = export_sharegpt_format(filtered)
    print(f"  ShareGPT format: {len(sharegpt)} conversations (multi-turn)")
    print()

    # ── Step 4: Fine-tuning commands ────────────────────────────────
    print("Step 4: Fine-tune with Unsloth (run these commands):")
    print()
    print(f"  # Fine-tune for {taxonomy.name.lower()} domain")
    print("  unsloth train \\")
    print("    --model unsloth/Llama-3.1-8B \\")
    print(f"    --data training_data/{taxonomy.name.lower()}_alpaca.json \\")
    print(f"    --output ./{taxonomy.name.lower()}-model \\")
    print("    --epochs 3 --lr 2e-5 --lora-r 16")
    print()

    # ── Summary ─────────────────────────────────────────────────────
    difficulty_counts: dict[str, int] = {}
    for p in filtered:
        d = p.get("difficulty", "unknown")
        difficulty_counts[d] = difficulty_counts.get(d, 0) + 1

    print("=" * 60)
    print("  Pipeline Summary")
    print("=" * 60)
    print(f"  Domain:           {taxonomy.name}")
    print(f"  Training pairs:   {len(filtered)}")
    print(f"  Difficulty split: {difficulty_counts}")
    print(f"  Generation cost:  ${tracker.total_cost:.4f}")
    print(f"  After fine-tune:  $0.00/token (local inference)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
