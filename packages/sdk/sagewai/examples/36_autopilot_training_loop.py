#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 36 — The autopilot training loop closes.

**Freemium boundary:** the production autopilot loop that this example
illustrates uses the hosted ``sagewai-llm`` service (default:
``api.sagewai.ai``) or a local copy of the ``sagewai/sagewai-llm`` repo
running on ``127.0.0.1:8100`` for blueprint generation. The simulation
below is offline — synthetic mission runs feed the Curator so you can
inspect the captured-data surface without a service. The other 32
examples in this directory run with no hosted service — pure OSS path.

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

Real-world use cases:

- Senior platform engineer at a 200-person fintech SaaS — you shipped
  the support-ticket triage agent on Haiku in Q1. The CFO asked you
  to bring the cost down by 50% in Q3. The Curator's training-data
  hooks accumulate every accepted triage decision automatically; once
  there are 500 accepted samples a fine-tune job triggers and the
  cheaper local model lands in the routing tier.
- ML engineer at a 100-person AI-feature SaaS — you run agents in
  production. Your CTO has asked "can we get cheaper, faster,
  more-specialised models from our own usage?" without manually
  labelling anything. This is the closes-the-loop story.
- Senior backend engineer at a 250-person legaltech SaaS — every
  human override on the contract-clause classifier should improve the
  next model version. The training loop turns operator behaviour into
  the next training set; you stop paying frontier-model rates for the
  routine 80% of clauses.
- Engineering manager at a 350-person devtools company — your support
  team rates AI replies 1-5 daily. The training loop turns those
  ratings into the quality filter (``user_rating >= 4``) that decides
  what's worth fine-tuning on. The team's daily QA work is the
  training corpus.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
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


# ── cycle-2 helpers ────────────────────────────────────────────────


def _find_latest_instance_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [d for d in root.iterdir() if d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _load_captured_runs(instance_dir: Path) -> list[tuple[MissionRunResult, dict]]:
    """Load JSONL captures from a training_runs instance directory."""
    runs: list[tuple[MissionRunResult, dict]] = []
    if not instance_dir.exists():
        return runs
    for path in sorted(instance_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            sample = json.loads(line)
            run = MissionRunResult(
                mission_id=sample["mission_id"],
                status=sample.get("status", "completed"),
                steps=(
                    StepResult(
                        node_id="classifier",
                        status=sample.get("status", "completed"),
                        output=sample.get("completion"),
                        output_preview=(sample.get("completion") or "")[:200],
                        model_used=sample.get("model_used"),
                    ),
                ),
                duration_seconds=float(sample.get("duration_seconds", 1.0)),
            )
            ctx = {
                "project_id": sample.get("project_id", "captured"),
                "user_rating": sample.get("user_rating", 5),
                "human_override": sample.get("human_override", False),
            }
            runs.append((run, ctx))
    return runs


def _synthetic_runs_seed_b() -> list[tuple[MissionRunResult, dict]]:
    """Second synthetic seed — used when no captured runs exist for cycle-2."""
    return [
        (
            _synthetic_run(
                mission_id="m-b01",
                output='{"urgency": "high", "reason": "db connection pool exhausted"}',
            ),
            {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
        ),
        (
            _synthetic_run(
                mission_id="m-b02",
                output='{"urgency": "medium", "reason": "deployment question"}',
            ),
            {"project_id": "acme-prod", "user_rating": 4, "human_override": False},
        ),
        (
            _synthetic_run(
                mission_id="m-b03",
                output='{"urgency": "low", "reason": "documentation request"}',
            ),
            {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
        ),
        (
            _synthetic_run(
                mission_id="m-b04",
                output='{"urgency": "high", "reason": "data pipeline backlog"}',
            ),
            {"project_id": "acme-prod", "user_rating": 5, "human_override": False},
        ),
        (
            # FILTERED OUT — human override
            _synthetic_run(
                mission_id="m-b05",
                output='{"urgency": "medium", "reason": "test alert"}',
            ),
            {"project_id": "acme-prod", "user_rating": 2, "human_override": True},
        ),
    ]


def _run_cycle(
    label: str,
    blueprint: Blueprint,
    runs: list[tuple[MissionRunResult, dict]],
    curator: Curator,
) -> None:
    """Feed runs through the curator and print the per-run outcome table."""
    print(f"  {'mission':<12} {'rating':>6} {'override':>8}  outcome")
    print(f"  {'-'*12} {'-'*6} {'-'*8}  {'-'*40}")
    for run, ctx in runs:
        added = curator.process(run, blueprint, ctx)
        rating = ctx.get("user_rating", "—")
        override = ctx.get("human_override", "—")
        outcome = f"accepted into {added[0]}" if added else "filtered (quality_filter)"
        print(f"  {run.mission_id:<12} {rating:>6} {str(override):>8}  {outcome}")
    print()


def _run_live_fine_tune(curator: Curator, instance_id: str) -> Path | None:
    """Run mlx_lm.lora on the cycle-2 dataset. Returns adapter path or None.

    Per training-loop-deploy-strategy.md: 100-iter pass on Apple Silicon,
    base model llama3.2:3b-instruct (small + permissive), adapter output
    under ~/.sagewai/adapters/{instance_id}-cycle-2/.
    """
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        print("  not Apple Silicon — skipping live fine-tune.")
        return None
    try:
        import mlx_tune  # noqa: F401
    except ImportError:
        try:
            import mlx_lm  # noqa: F401
        except ImportError:
            print(
                "  skipping live fine-tune — "
                "install mlx-lm[lora] (or mlx-tune) to enable."
            )
            return None

    out_dir = Path.home() / ".sagewai" / "adapters" / f"{instance_id}-cycle-2"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write the dataset in the format mlx_lm.lora expects.
    dataset_path = out_dir / "train.jsonl"
    samples = []
    for ds in curator.datasets.values():
        samples.extend(ds.samples)
    if not samples:
        print("  no samples — skipping live fine-tune.")
        return None
    dataset_path.write_text(
        "\n".join(json.dumps(s) for s in samples) + "\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable, "-m", "mlx_lm.lora",
        "--model", "mlx-community/Llama-3.2-3B-Instruct-4bit",
        "--data", str(out_dir),
        "--adapter-path", str(out_dir / "adapter"),
        "--iters", "100",
        "--batch-size", "1",
        "--train",
    ]
    print(f"  launching: {' '.join(cmd)}")
    import subprocess
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("  fine-tune exceeded 10-min timeout — aborting.")
        return None
    if proc.returncode != 0:
        print(f"  fine-tune failed: {proc.stderr[:200]}")
        return None
    return out_dir / "adapter"


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

    curator = Curator(config=CuratorConfig())

    # ── Cycle 1 — synthetic seed ────────────────────────────────────
    print("─" * 72)
    print(" Cycle 1 — synthetic seed")
    print("─" * 72)
    print()
    _run_cycle("cycle-1", blueprint, SYNTHETIC_RUNS, curator)

    cycle_1_jobs = curator.clear_pending_jobs()
    if cycle_1_jobs:
        for job in cycle_1_jobs:
            print(f"  cycle-1 job_id={job.job_id} dataset={job.dataset_id}")
    else:
        print("  No cycle-1 fine-tune jobs triggered.")
    print()

    # ── Cycle 2 — captured runs (or fallback synthetic seed-B) ─────
    print("─" * 72)
    print(" Cycle 2 — captured runs from ~/.sagewai/training_runs/")
    print("─" * 72)
    print()
    runs_root = Path.home() / ".sagewai" / "training_runs"
    instance_dir = _find_latest_instance_dir(runs_root)
    if instance_dir is None:
        print(f"  no captured runs found under {runs_root} — using seed-B fallback.")
        cycle_2_runs = _synthetic_runs_seed_b()
        instance_id_for_ft = "synthetic-cycle-2"
    else:
        cycle_2_runs = _load_captured_runs(instance_dir)
        instance_id_for_ft = instance_dir.name
        print(f"  captured runs loaded from {instance_dir} ({len(cycle_2_runs)} runs)")
    if not cycle_2_runs:
        print("  fallback: synthetic seed-B")
        cycle_2_runs = _synthetic_runs_seed_b()
    _run_cycle("cycle-2", blueprint, cycle_2_runs, curator)

    cycle_2_jobs = curator.clear_pending_jobs()
    if cycle_2_jobs:
        for job in cycle_2_jobs:
            print(f"  cycle-2 job_id={job.job_id} dataset={job.dataset_id}")
    else:
        print("  No cycle-2 fine-tune jobs triggered (sample threshold not met).")
    print()

    # ── Live fine-tune (gated by SAGEWAI_FT_LIVE) ─────────────────
    print("─" * 72)
    print(" Live fine-tune")
    print("─" * 72)
    print()
    if os.environ.get("SAGEWAI_FT_LIVE", "") not in ("1", "true", "yes"):
        print("  (SAGEWAI_FT_LIVE not set — printing FineTuneJob payload only.)")
        all_jobs = cycle_1_jobs + cycle_2_jobs
        for job in all_jobs:
            print(f"  would run: base={job.base_model} method={job.method}")
        if not all_jobs:
            print("  (no jobs queued — sample threshold not met in either cycle)")
    else:
        adapter_path = _run_live_fine_tune(curator, instance_id_for_ft)
        if adapter_path:
            print(f"  adapter saved to: {adapter_path}")
    print()

    # ── The loop closes ────────────────────────────────────────────
    print("─" * 72)
    print(" The loop closes")
    print("─" * 72)
    print()
    total_samples = sum(d.sample_count for d in curator.datasets.values())
    print(f"  cycle-1 + cycle-2 produced {total_samples} accepted samples.")
    print(
        f"  cycle-1 jobs: {len(cycle_1_jobs)}; cycle-2 jobs: {len(cycle_2_jobs)}."
    )
    print(
        "  Run example 30 first to seed real triage runs into "
        "~/.sagewai/training_runs/."
    )


if __name__ == "__main__":
    asyncio.run(main())
