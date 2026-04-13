#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 25 — Training Data Pipeline: Collect, Curate, Export for Unsloth.

Demonstrates the full training data lifecycle in Sagewai:

1. **Collect** — save agent conversation pairs as training samples
2. **Rate** — quality-score each sample (1-5) for curation
3. **Export** — download as JSONL in Alpaca or ShareGPT format
4. **Fine-tune** — feed directly to Unsloth for LoRA fine-tuning
5. **Deploy** — push fine-tuned model to Ollama, route traffic

Everything is **project-scoped** — each project collects its own
training data and trains its own domain LLM.

**This is one of Sagewai's key moats**: per-project fine-tuning
creates a flywheel where agents generate data → humans curate →
model improves → agents get better → more data → better model.

Requirements::

    pip install sagewai

Usage::

    python examples/25_training_data_pipeline.py

    # Or with the admin API running:
    curl http://localhost:8000/api/v1/training/export?format=alpaca > training.jsonl
"""

from __future__ import annotations

import asyncio
import json


async def main() -> None:
    print("=" * 60)
    print("  Sagewai Training Data Pipeline")
    print("  Collect → Curate → Export → Fine-tune → Deploy")
    print("=" * 60)
    print()

    # ── Step 1: Simulate agent conversations as training samples ──

    training_samples = [
        {
            "agent_name": "healthcare-assistant",
            "model": "gpt-4o",
            "input_text": "What are the common side effects of metformin?",
            "output_text": (
                "Common side effects of metformin include: nausea, diarrhea, "
                "stomach pain, and metallic taste. These usually improve after "
                "a few weeks. Rare but serious: lactic acidosis (seek immediate "
                "medical attention for muscle pain, difficulty breathing, or "
                "unusual fatigue)."
            ),
            "quality": 5,
            "tags": ["medical", "pharmacology"],
            "is_example": True,
            "project_id": "healthcare",
        },
        {
            "agent_name": "healthcare-assistant",
            "model": "gpt-4o",
            "input_text": "Explain the difference between Type 1 and Type 2 diabetes.",
            "output_text": (
                "Type 1: autoimmune destruction of beta cells, no insulin "
                "production, requires insulin injections, typically childhood "
                "onset. Type 2: insulin resistance + relative deficiency, "
                "managed with lifestyle + oral medications initially, typically "
                "adult onset but increasing in children."
            ),
            "quality": 5,
            "tags": ["medical", "endocrinology"],
            "is_example": True,
            "project_id": "healthcare",
        },
        {
            "agent_name": "healthcare-assistant",
            "model": "gpt-4o",
            "input_text": "What is a normal blood pressure reading?",
            "output_text": "Normal is below 120/80 mmHg.",
            "quality": 2,  # Too brief — low quality
            "tags": ["medical", "cardiology"],
            "is_example": True,
            "project_id": "healthcare",
        },
    ]

    print(f"Step 1: Collected {len(training_samples)} conversation pairs")
    for s in training_samples:
        stars = "★" * s["quality"] + "☆" * (5 - s["quality"])
        print(f"  [{stars}] {s['input_text'][:50]}...")
    print()

    # ── Step 2: Filter by quality ──

    high_quality = [s for s in training_samples if s["quality"] >= 4]
    print(f"Step 2: {len(high_quality)} samples pass quality threshold (≥4)")
    print()

    # ── Step 3: Export as Alpaca JSONL ──

    print("Step 3: Export as Alpaca format JSONL")
    print()

    alpaca_lines = []
    for s in high_quality:
        entry = {
            "instruction": "You are a medical AI assistant. Answer accurately and cite guidelines when possible.",
            "input": s["input_text"],
            "output": s["output_text"],
        }
        alpaca_lines.append(json.dumps(entry, ensure_ascii=False))

    print("  training-data-alpaca.jsonl:")
    for line in alpaca_lines:
        parsed = json.loads(line)
        print(f"    input: {parsed['input'][:40]}...")
        print(f"    output: {parsed['output'][:40]}...")
        print()

    # ── Step 4: Export as ShareGPT JSONL ──

    print("Step 4: Export as ShareGPT format (multi-turn)")
    print()

    sharegpt_lines = []
    for s in high_quality:
        entry = {
            "conversations": [
                {"from": "human", "value": s["input_text"]},
                {"from": "gpt", "value": s["output_text"]},
            ]
        }
        sharegpt_lines.append(json.dumps(entry, ensure_ascii=False))

    print(f"  {len(sharegpt_lines)} ShareGPT entries ready")
    print()

    # ── Step 5: Fine-tuning commands ──

    print("Step 5: Fine-tune with Unsloth")
    print()
    print("  # Install Unsloth:")
    print("  pip install unsloth")
    print()
    print("  # Fine-tune (from Python):")
    print("  from sagewai.intelligence.unsloth import UnslothFineTuner")
    print("  tuner = UnslothFineTuner(")
    print('      base_model="unsloth/Llama-3.2-1B-Instruct",')
    print('      dataset_path="training-data-alpaca.jsonl",')
    print("      lora_rank=16,")
    print("      epochs=3,")
    print("  )")
    print("  tuner.train()")
    print('  tuner.save("./healthcare-llm")')
    print()

    # ── Step 6: Deploy to Ollama ──

    print("Step 6: Deploy to Ollama")
    print()
    print("  # Create Modelfile:")
    print('  echo "FROM ./healthcare-llm" > Modelfile')
    print("  ollama create healthcare-assistant -f Modelfile")
    print()
    print("  # Verify:")
    print("  ollama list  # → healthcare-assistant:latest")
    print()

    # ── Step 7: Route traffic ──

    print("Step 7: Route project traffic to fine-tuned model")
    print()
    print("  # In admin panel: System → AI Models")
    print("  # Add provider: Ollama, model: healthcare-assistant")
    print("  # Set as project default model")
    print()
    print("  # Now all agents in the 'healthcare' project use the")
    print("  # fine-tuned model at $0/token — no cloud API needed!")
    print()

    # ── Summary ──

    print("=" * 60)
    print("  Training Data Pipeline Complete")
    print(f"  Samples: {len(training_samples)} collected, {len(high_quality)} exported")
    print("  Format: Alpaca + ShareGPT JSONL")
    print("  Project: healthcare (scoped)")
    print("  Cost: $0/token after fine-tuning")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
