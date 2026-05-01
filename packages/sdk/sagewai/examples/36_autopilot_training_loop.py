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
"""Example 36 — The autopilot training loop closes.

The closes-the-loop story for the Five Pillars: every mission run becomes
training data for the next model. Operators get cheaper, faster, more
specialised models without manually labelling anything.

Flow:

1. A blueprint declares ``training_data_hooks`` (where mission output
   accumulates) and a ``learning_loop_target`` (when to fine-tune).
2. Operators run missions. Some pass quality filters
   (``user_rating >= 4``, ``human_override == false``); those become
   samples in the training dataset.
3. After ``trigger_after_labeled_samples`` accumulate, Curator emits a
   :class:`FineTuneJob` — ready for Unsloth / Ollama / Lambda labs.
4. The fine-tuned model lands in the routing tier. Future missions
   prefer it for its niche, falling back to the cloud only when needed.

We simulate **8 mission runs** of an email-triage agent. Six pass the
quality filter (rated ≥4), two don't. After 5 accepted samples a fine-
tune job triggers. Output lists the dataset, the job, and exports the
samples as Alpaca-format JSONL.

No live LLM calls — runs are synthetic so the demo is offline + fast.
The hooks Curator listens to are real (same code as production).

Requirements::

    pip install 'sagewai[autopilot]'

Usage::

    python 36_autopilot_training_loop.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from sagewai.autopilot.agent_graph import AgentGraph, Agent
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.types import (
    MissionRunResult,
    StepResult,
)
from sagewai.autopilot.curator import Curator
from sagewai.autopilot.curator.types import CuratorConfig
from sagewai.autopilot.models import (
    EvalRef,
    LearningLoopConfig,
    Metric,
    TrainingHook,
)


# ── 1. The blueprint that declares the training loop ──────────────


def _build_email_triage_blueprint() -> Blueprint:
    """A blueprint with training-data hooks + a learning-loop target."""
    return Blueprint(
        id="email-triage-v1",
        version="1.0.0",
        title="Email Triage",
        description="Classifies incoming emails into urgency tiers.",
        category="customer-success",
        mode="event_driven",
        example_goals=("Triage incoming customer-support emails by urgency",),
        required_slots={},
        optional_slots={},
        providers_required=(),
        agent_graph=AgentGraph(
            nodes=(
                Agent(
                    id="classifier",
                    kind="llm",
                    role="classifier",
                    prompt_ref="email_triage.classifier.v1",
                    tools=(),
                    output_schema_ref=None,
                    max_steps=1,
                    deterministic_fallback=False,
                ),
            ),
            edges=(),
            branches={},
            entry="classifier",
        ),
        success_criteria=EvalRef(
            dataset_id="email-triage-eval-v1",
            metrics=(Metric(name="accuracy", op=">=", value=0.92),),
        ),
        training_data_hooks=(
            TrainingHook(
                event="classifier.completed",
                # The {project_id} placeholder lets each tenant
                # accumulate its own dataset for its own fine-tune.
                dataset="email-triage-{project_id}",
                format="alpaca",
                quality_filter="user_rating >= 4 AND human_override == False",
            ),
        ),
        learning_loop_target=LearningLoopConfig(
            trigger_after_labeled_samples=5,  # small for the demo
            base_model="ollama/llama3:8b",
            eval_gate_dataset_id="email-triage-eval-v1",
            promotion_criteria="accuracy >= 0.92 AND cost_per_call <= 0.001",
            fine_tune_method="unsloth",
            deploy_as="ollama",
        ),
    )


# ── 2. Synthetic mission runs (8 total — 6 pass the filter) ──────


def _synthetic_run(
    *, mission_id: str, output: str, status: str = "completed",
) -> MissionRunResult:
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        steps=(
            StepResult(
                node_id="classifier",
                status=status,
                output=output,
                output_preview=output[:200],
                model_used="claude-haiku-4-5-20251001",
            ) if status == "completed" else
            StepResult(
                node_id="classifier",
                status=status,
                output=None,
                output_preview="(failed)",
                model_used="claude-haiku-4-5-20251001",
            ),
        ),
        duration_seconds=1.4,
    )


SYNTHETIC_RUNS: list[tuple[MissionRunResult, dict]] = [
    # (run, context). Context carries quality filter inputs.
    (
        _synthetic_run(
            mission_id="m-001",
            output='{"urgency": "high", "reason": "billing dispute"}',
        ),
        {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
    ),
    (
        _synthetic_run(
            mission_id="m-002",
            output='{"urgency": "low", "reason": "feature request"}',
        ),
        {"project_id": "acme-prod", "user_rating": 4, "human_override": False},
    ),
    (
        # FILTERED OUT — user marked it as wrong
        _synthetic_run(
            mission_id="m-003",
            output='{"urgency": "low", "reason": "spam"}',
        ),
        {"project_id": "acme-prod", "user_rating": 2, "human_override": True},
    ),
    (
        _synthetic_run(
            mission_id="m-004",
            output='{"urgency": "medium", "reason": "integration question"}',
        ),
        {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
    ),
    (
        _synthetic_run(
            mission_id="m-005",
            output='{"urgency": "high", "reason": "outage report"}',
        ),
        {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
    ),
    (
        _synthetic_run(
            mission_id="m-006",
            output='{"urgency": "low", "reason": "thank-you note"}',
        ),
        {"project_id": "acme-prod", "user_rating": 4, "human_override": False},
    ),
    (
        # FILTERED OUT — rating too low
        _synthetic_run(
            mission_id="m-007",
            output='{"urgency": "high", "reason": "test"}',
        ),
        {"project_id": "acme-prod", "user_rating": 3, "human_override": False},
    ),
    (
        _synthetic_run(
            mission_id="m-008",
            output='{"urgency": "medium", "reason": "renewal question"}',
        ),
        {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
    ),
]


# ── main ───────────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai Autopilot — training loop closes (example 36)")
    print("─" * 72)
    print()

    blueprint = _build_email_triage_blueprint()
    print(f"  Blueprint: {blueprint.id} v{blueprint.version}")
    print(f"  Hook: {blueprint.training_data_hooks[0].event}")
    print(f"  Filter: {blueprint.training_data_hooks[0].quality_filter}")
    target = blueprint.learning_loop_target
    assert target is not None
    print(f"  Trigger: {target.trigger_after_labeled_samples} accepted samples")
    print(f"  Base model: {target.base_model}")
    print(f"  Method: {target.fine_tune_method} → {target.deploy_as}")
    print()

    # 2. Curator processes runs against the blueprint
    curator = Curator(config=CuratorConfig())

    print("─" * 72)
    print(" Mission run-by-run")
    print("─" * 72)
    print()
    print(f"  {'mission':<8} {'rating':>6} {'override':>8}  outcome")
    print(f"  {'-'*8} {'-'*6} {'-'*8}  {'-'*40}")

    for run, ctx in SYNTHETIC_RUNS:
        added = curator.process(run, blueprint, ctx)
        rating = ctx.get("user_rating", "—")
        override = ctx.get("human_override", "—")
        if added:
            outcome = f"accepted into {added[0]}"
        else:
            outcome = "filtered (failed quality_filter)"
        print(
            f"  {run.mission_id:<8} {rating:>6} {str(override):>8}  {outcome}"
        )
    print()

    # 3. Show the dataset state
    print("─" * 72)
    print(" Training dataset state")
    print("─" * 72)
    print()
    for ds_id, ds in curator.datasets.items():
        print(f"  dataset_id   = {ds_id}")
        print(f"  project_id   = {ds.project_id}")
        print(f"  format       = {ds.format}")
        print(f"  sample_count = {ds.sample_count}")
        print()

    # 4. Show the fine-tune job that triggered
    pending = curator.clear_pending_jobs()
    print("─" * 72)
    print(" Fine-tune jobs")
    print("─" * 72)
    print()
    if not pending:
        print("  No fine-tune jobs triggered (sample threshold not met).")
    else:
        for job in pending:
            print(f"  job_id          = {job.job_id}")
            print(f"  dataset_id      = {job.dataset_id}")
            print(f"  project_id      = {job.project_id}")
            print(f"  base_model      = {job.base_model}")
            print(f"  method          = {job.method}")
            print(f"  deploy_as       = {job.deploy_as}")
            print(f"  status          = {job.status}")
            print()

    # 5. Export as Alpaca JSONL — ready for Unsloth fine-tuning
    print("─" * 72)
    print(" Export — Alpaca JSONL (Unsloth-ready)")
    print("─" * 72)
    print()
    with tempfile.TemporaryDirectory(prefix="sagewai-train-") as tmp:
        for ds_id, ds in curator.datasets.items():
            jsonl_path = Path(tmp) / f"{ds_id}.jsonl"
            jsonl_path.write_text(
                "\n".join(json.dumps(s) for s in ds.samples) + "\n",
                encoding="utf-8",
            )
            print(f"  wrote {jsonl_path}")
            print(f"  size  {jsonl_path.stat().st_size} bytes")
            print()
            print("  First sample:")
            print(f"    {ds.samples[0]}")
            print()
            print("  Last sample:")
            print(f"    {ds.samples[-1]}")
            print()

    # 6. The closing-the-loop summary
    print("─" * 72)
    print(" The loop closes")
    print("─" * 72)
    print()
    print("  Operators ran 8 missions on cloud Haiku — paid Anthropic per call.")
    print(f"  {sum(d.sample_count for d in curator.datasets.values())} runs passed the quality filter and became training samples.")
    print(f"  {len(pending)} fine-tune job(s) ready to dispatch to Unsloth.")
    print(f"  Once trained, future missions route to ollama/llama3:8b — $0/token.")
    print()
    print("  This is the cost-down story in numbers: same workload, every")
    print("  iteration cheaper. Sagewai owns the labelled data; you keep")
    print("  the model.")


if __name__ == "__main__":
    asyncio.run(main())
