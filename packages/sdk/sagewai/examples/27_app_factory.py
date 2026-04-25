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
"""Example 27 — App Factory: Slack → research → build → PR.

The first of four dark-factory tenants running inside one Sagewai
instance. This example lives in ``project_id="app-factory"`` and turns
a one-line Slack brief into a working (mock) repository, all without
human intervention except for a final merge gate.

**What this shows**

1. **Multi-tenant envelope** — bootstraps all four tenants, but only
   operates inside ``app-factory``; asserts at the end that the other
   three tenants saw zero traffic from this run.
2. **Fleet dispatch** — every pipeline stage is enqueued through the
   shared ``FleetDispatcher`` with ``project_id=app-factory`` labels.
   A dedicated worker in the ``build`` pool claims and runs the work;
   the shared overflow worker can pick up anything the dedicated
   runner is too busy to serve.
3. **Reflexion loop** — the build stage runs a first pass, self-critiques,
   and patches. Shown for frontend/backend/infra/tests subsystems.
4. **Approval gate with trust graduation** — the final draft-PR merge
   hits an ``ApprovalGate``. On the first run the gate is consulted;
   on subsequent runs the tenant's trust score has graduated and the
   gate auto-passes.
5. **Training flywheel** — every stage writes a training sample to the
   tenant-scoped store. At the end the example exports Alpaca JSONL,
   runs the stub Unsloth pass, registers a "trained" local tier, and
   re-runs a mini batch to show the per-task cost delta.

**Local-first.** The default model for this tenant is
``qwen2.5-coder:7b`` via Ollama; LLM calls are mocked out so the
example runs offline in CI. Set ``FACTORIES_ALLOW_CLOUD=1`` to relax
the Ollama preflight, ``FACTORIES_LIVE=1`` to wire real connectors.

Requirements::

    pip install sagewai

Usage::

    python packages/sdk/sagewai/examples/27_app_factory.py

Typical output::

    [app-factory] brief: "build a todo app with auth, postgres, react"
    [app-factory] intake      ✓ classified: web-saas (0.15s, $0.0020)
    [app-factory] research    ✓ 4 sub-tasks fused (0.62s, $0.0080)
    [app-factory] plan        ✓ PRD + mermaid architecture (0.21s, $0.0020)
    [app-factory] scaffold    ✓ repo opened at /tmp/.../artifacts/...
    [app-factory] build       ✓ 4 subsystems, 2-pass reflexion (1.11s, $0.0160)
    [app-factory] delivery    ✓ draft PR #42, gate: auto-approved
    ...
    Tenant totals:
      app-factory      11 tasks  $0.0320
      biz-ops           0 tasks  $0.0000
      wealth-desk       0 tasks  $0.0000
      school-mentor     0 tasks  $0.0000

    Training flywheel:
      collected 11 samples → exported → stub-trained qwen2.5-coder:7b-trained-app-factory
      cost on 5-task re-run: before=$0.0100  after=$0.0005  (-95.0%)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sagewai.examples._factory import (
    ApprovalGate,
    FleetScoreboard,
    TrainingSample,
    WorkItem,
    bootstrap,
    collect_sample,
    register_trained_tier,
    run_unsloth_stub,
    seed_fleet,
)
from sagewai.examples._factory.train_tenant import reset as reset_training
from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
)
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import NetworkPolicy, SandboxMode

TENANT = "app-factory"
ORG_ID = "factory-demo"


# ── Tenant cost model (before training) ──────────────────────────────


# Cost-per-task model. "before training" numbers are what a cloud call
# would cost; "after training" is what a fine-tuned local model charges
# — effectively just GPU-second amortisation.
_COST_BEFORE = {
    "intake": 0.002,
    "research": 0.002,
    "plan": 0.002,
    "scaffold": 0.001,
    "build": 0.004,
    "delivery": 0.001,
}
_COST_AFTER = {k: v * 0.05 for k, v in _COST_BEFORE.items()}


# ── Stage workers — deterministic mocks of the real thing ────────────


@dataclass
class StageResult:
    """What a pipeline stage hands back to the orchestrator."""

    name: str
    output: dict[str, Any]
    duration_s: float
    cost_usd: float
    training_prompt: str
    training_completion: str
    artifact_path: Path | None = None


def _classify_platform(brief: str) -> str:
    """Simulated intake classifier.

    Real version would run a UniversalAgent with structured output. The
    deterministic keyword match here keeps the example offline and
    repeatable without changing the shape of the data that flows into
    subsequent stages.
    """
    text = brief.lower()
    if any(k in text for k in ("mobile", "ios", "android")):
        return "mobile"
    if any(k in text for k in ("iot", "sensor", "firmware")):
        return "iot"
    if any(k in text for k in ("api", "service", "microservice")):
        return "api-only"
    return "web-saas"


async def stage_intake(brief: str, *, cost_table: dict[str, float]) -> StageResult:
    await asyncio.sleep(0.03)
    platform = _classify_platform(brief)
    return StageResult(
        name="intake",
        output={"platform": platform, "brief": brief},
        duration_s=0.15,
        cost_usd=cost_table["intake"],
        training_prompt=f"Classify the target platform for this brief: {brief}",
        training_completion=platform,
    )


async def stage_research(
    brief: str,
    platform: str,
    *,
    cost_table: dict[str, float],
) -> StageResult:
    await asyncio.sleep(0.05)
    sub_tasks = ("market", "competitors", "stack", "cost-model")
    insights = {
        "market": (
            "TAM: $8.2B for productivity tools; 14% YoY growth; "
            "mid-market underserved."
        ),
        "competitors": (
            "3 direct (todoist, things3, tickticks), differentiator is "
            "team-first auth."
        ),
        "stack": (
            "Next.js + tRPC + Postgres; Auth.js; Vercel deploy; pgcrypto "
            "for at-rest enc."
        ),
        "cost-model": (
            "$0.18/MAU hosting + $0.02/MAU LLM; breakeven at 12k MAU on "
            "$9 pro tier."
        ),
    }
    fused = {k: insights[k] for k in sub_tasks}
    return StageResult(
        name="research",
        output={"platform": platform, "insights": fused},
        duration_s=0.62,
        cost_usd=cost_table["research"] * len(sub_tasks),
        training_prompt=(
            f"Research this {platform} idea and produce market, competitor, "
            f"stack, and cost-model insights. Brief: {brief}"
        ),
        training_completion=json.dumps(fused, indent=2),
    )


async def stage_plan(
    research: dict[str, Any],
    *,
    cost_table: dict[str, float],
    artifacts_dir: Path,
) -> StageResult:
    await asyncio.sleep(0.03)
    concept_dir = artifacts_dir / "concept"
    concept_dir.mkdir(parents=True, exist_ok=True)

    prd = concept_dir / "PRD.md"
    prd.write_text(
        "# Product Requirements Document\n\n"
        f"**Platform:** {research['platform']}\n\n"
        "## Problem\n\nTeam todo lists keep drifting out of sync with\n"
        "reality. Users want a shared source of truth with auth.\n\n"
        "## Core features\n\n"
        "- Org-scoped workspaces\n- Magic-link auth\n- Realtime sync\n"
        "- Permission roles: owner, editor, viewer\n\n"
        "## Out of scope\n\n- Gantt charts\n- Time tracking\n"
    )
    architecture = concept_dir / "architecture.md"
    architecture.write_text(
        "# Architecture\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "  user --> next[Next.js app]\n"
        "  next --> trpc[tRPC API]\n"
        "  trpc --> pg[(Postgres)]\n"
        "  trpc --> auth[Auth.js]\n"
        "  pg --> backup[(S3 backup)]\n"
        "```\n"
    )
    return StageResult(
        name="plan",
        output={
            "prd_path": str(prd),
            "architecture_path": str(architecture),
        },
        duration_s=0.21,
        cost_usd=cost_table["plan"],
        training_prompt=(
            "Turn the following research bundle into a PRD plus a mermaid "
            f"architecture diagram: {json.dumps(research['insights'])}"
        ),
        training_completion=prd.read_text() + "\n\n---\n\n" + architecture.read_text(),
        artifact_path=concept_dir,
    )


async def stage_scaffold(
    plan: dict[str, Any],
    *,
    cost_table: dict[str, float],
    artifacts_dir: Path,
) -> StageResult:
    await asyncio.sleep(0.02)
    repo_dir = artifacts_dir / "repo"
    (repo_dir / "apps" / "web").mkdir(parents=True, exist_ok=True)
    (repo_dir / "packages" / "db").mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text("# Todo Factory output\n")
    (repo_dir / "package.json").write_text('{"name": "todo-factory", "private": true}\n')
    return StageResult(
        name="scaffold",
        output={"repo_dir": str(repo_dir)},
        duration_s=0.08,
        cost_usd=cost_table["scaffold"],
        training_prompt=(
            f"Scaffold the initial repo layout for the PRD at {plan['prd_path']}"
        ),
        training_completion="apps/web, packages/db, README.md, package.json",
        artifact_path=repo_dir,
    )


async def _build_subsystem(name: str) -> tuple[int, str]:
    """First pass intentionally fails a test; reflexion fixes it."""
    await asyncio.sleep(0.02)
    first_pass_issue = {
        "frontend": "missing loading state on /inbox",
        "backend": "auth middleware not applied to /api/todos",
        "infra": "pg_data volume not persistent in docker-compose",
        "tests": "seed fixture referenced dropped column",
    }[name]
    # Second pass: fixed.
    await asyncio.sleep(0.02)
    fix_summary = f"{name}: patched ({first_pass_issue}) — all checks green"
    return 2, fix_summary  # 2 passes


async def stage_build(
    scaffold: dict[str, Any],
    *,
    cost_table: dict[str, float],
    artifacts_dir: Path,
) -> StageResult:
    subsystems = ("frontend", "backend", "infra", "tests")
    results = await asyncio.gather(*[_build_subsystem(s) for s in subsystems])

    total_passes = sum(r[0] for r in results)
    summaries = [r[1] for r in results]

    build_log = artifacts_dir / "build.log"
    build_log.write_text("\n".join(summaries) + "\n")

    return StageResult(
        name="build",
        output={
            "subsystems": list(subsystems),
            "total_reflexion_passes": total_passes,
            "build_log": str(build_log),
        },
        duration_s=1.11,
        cost_usd=cost_table["build"] * len(subsystems),
        training_prompt=(
            "Build these subsystems for the scaffolded repo, patching any "
            f"first-pass issues via reflexion: {', '.join(subsystems)}"
        ),
        training_completion="\n".join(summaries),
    )


async def stage_delivery(
    build: dict[str, Any],
    *,
    cost_table: dict[str, float],
    gate: ApprovalGate,
    work_item_id: str,
) -> StageResult:
    await asyncio.sleep(0.02)
    pr_number = 42
    gate_outcome = await gate.check(
        work_item_id=work_item_id,
        action="merge-draft-pr",
        severity=4,
        summary=(
            f"Draft PR #{pr_number} ready — "
            f"{len(build['subsystems'])} subsystems, "
            f"{build['total_reflexion_passes']} reflexion passes"
        ),
    )
    return StageResult(
        name="delivery",
        output={
            "pr_number": pr_number,
            "gate_decision": gate_outcome.decision.value,
            "gate_reason": gate_outcome.reason,
        },
        duration_s=0.09,
        cost_usd=cost_table["delivery"],
        training_prompt=(
            f"Summarise this build for a draft PR description: {build}"
        ),
        training_completion=(
            f"Draft PR #{pr_number}: adds frontend/backend/infra/tests with "
            "green CI after a reflexion patch round. Ready for review."
        ),
    )


# ── Dispatcher-driven pipeline ───────────────────────────────────────


@dataclass
class Pipeline:
    """State passed between stages."""

    work_item: WorkItem
    artifacts_dir: Path
    cost_table: dict[str, float]
    results: list[StageResult] = field(default_factory=list)


async def _dispatch_stage(
    *,
    pipeline: Pipeline,
    stage_name: str,
    runner,
    task_store: InMemoryTaskStore,
    dispatcher: FleetDispatcher,
    worker_id: str,
    worker_name: str,
    models: list[str],
    pool: str,
    labels: dict[str, str],
    scoreboard: FleetScoreboard,
) -> StageResult:
    """Enqueue one stage as a fleet task and run it on the claimed worker."""
    task = {
        "run_id": f"{pipeline.work_item.id}-{stage_name}",
        "model": models[0],
        "pool": pool,
        # Only project_id goes in labels — the dispatcher matches every
        # task label against the worker, so any extra key would miss.
        "labels": {"project_id": TENANT},
        "payload": json.dumps(
            {**pipeline.work_item.to_task_metadata(), "stage": stage_name}
        ),
        "requires_sandbox_mode": SandboxMode.PER_RUN,
        "requires_image": f"ghcr.io/sagewai/sandbox-general:{image_manifest.SDK_VERSION}",
        "requires_network_policy": NetworkPolicy.FULL,
    }
    task_store.enqueue(task)

    claimed = await dispatcher.claim(
        worker_id=worker_id,
        org_id=ORG_ID,
        models_canonical=models,
        pool=pool,
        labels=labels,
    )
    if claimed is None or claimed["run_id"] != task["run_id"]:
        raise RuntimeError(
            f"dispatcher failed to route {stage_name} to {worker_name}"
        )

    t0 = time.perf_counter()
    result = await runner()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    await dispatcher.report(
        worker_id=worker_id,
        org_id=ORG_ID,
        run_id=claimed["run_id"],
        status="completed",
        output=json.dumps({"stage": stage_name, "cost": result.cost_usd}),
    )

    scoreboard.record(
        worker_id=worker_name,
        tenant=TENANT,
        duration_ms=elapsed_ms,
        cost_usd=result.cost_usd,
    )

    collect_sample(
        TrainingSample(
            tenant=TENANT,
            agent_name=f"app-factory-{stage_name}",
            model=models[0],
            input_text=result.training_prompt,
            output_text=result.training_completion,
            quality=5,
        )
    )

    pipeline.results.append(result)
    return result


async def run_app_factory(
    *,
    brief: str,
    task_store: InMemoryTaskStore,
    dispatcher: FleetDispatcher,
    dedicated_worker_id: str,
    dedicated_worker_name: str,
    dedicated_pool: str,
    dedicated_labels: dict[str, str],
    models: list[str],
    scoreboard: FleetScoreboard,
    gate: ApprovalGate,
    artifacts_dir: Path,
    cost_table: dict[str, float],
) -> Pipeline:
    """Run the app factory end-to-end for one brief."""
    work_item = WorkItem(
        tenant=TENANT,
        channel="slack",
        brief=brief,
        priority=2,
        metadata={"slack_channel": "#app-factory"},
    )
    pipeline = Pipeline(
        work_item=work_item,
        artifacts_dir=artifacts_dir,
        cost_table=cost_table,
    )

    print(f'[app-factory] brief: "{brief}"')

    async def run_stage(name: str, runner):
        result = await _dispatch_stage(
            pipeline=pipeline,
            stage_name=name,
            runner=runner,
            task_store=task_store,
            dispatcher=dispatcher,
            worker_id=dedicated_worker_id,
            worker_name=dedicated_worker_name,
            models=models,
            pool=dedicated_pool,
            labels=dedicated_labels,
            scoreboard=scoreboard,
        )
        print(
            f"[app-factory] {name:<10} ✓ "
            f"({result.duration_s:.2f}s, ${result.cost_usd:.4f})"
        )
        return result

    intake = await run_stage(
        "intake", lambda: stage_intake(brief, cost_table=cost_table)
    )
    research = await run_stage(
        "research",
        lambda: stage_research(
            brief, intake.output["platform"], cost_table=cost_table
        ),
    )
    plan = await run_stage(
        "plan",
        lambda: stage_plan(
            research.output,
            cost_table=cost_table,
            artifacts_dir=artifacts_dir,
        ),
    )
    scaffold = await run_stage(
        "scaffold",
        lambda: stage_scaffold(
            plan.output,
            cost_table=cost_table,
            artifacts_dir=artifacts_dir,
        ),
    )
    build = await run_stage(
        "build",
        lambda: stage_build(
            scaffold.output,
            cost_table=cost_table,
            artifacts_dir=artifacts_dir,
        ),
    )
    await run_stage(
        "delivery",
        lambda: stage_delivery(
            build.output,
            cost_table=cost_table,
            gate=gate,
            work_item_id=work_item.id,
        ),
    )

    return pipeline


# ── Isolation proof ──────────────────────────────────────────────────


async def assert_cross_tenant_isolation(
    scoreboard: FleetScoreboard,
) -> None:
    """Confirm no other tenant saw any work from this run."""
    other_tenants = {"biz-ops", "wealth-desk", "school-mentor"}
    totals = scoreboard.tenant_totals()
    leaked = sorted(t for t in other_tenants if t in totals and totals[t].tasks)
    if leaked:
        raise AssertionError(
            f"cross-tenant leak detected: other tenants saw tasks: {leaked}"
        )
    print(
        f"[app-factory] isolation ✓ other tenants untouched "
        f"({', '.join(sorted(other_tenants))})"
    )


# ── Training flywheel ────────────────────────────────────────────────


def _recompute_pipeline_cost(cost_table: dict[str, float]) -> float:
    """Sum the per-stage cost for a full pipeline run at the given table.

    ``research`` and ``build`` both fan out (4 sub-tasks each), so the
    totals mirror what ``run_app_factory`` actually spends.
    """
    return (
        cost_table["intake"]
        + cost_table["research"] * 4
        + cost_table["plan"]
        + cost_table["scaffold"]
        + cost_table["build"] * 4
        + cost_table["delivery"]
    )


async def run_training_flywheel(
    *,
    base_model: str,
    artifacts_dir: Path,
) -> dict[str, Any]:
    result = run_unsloth_stub(
        TENANT,
        base_model=base_model,
        output_dir=artifacts_dir / "training",
    )
    harness: dict[str, list[str]] = {TENANT: [base_model]}
    register_trained_tier(
        TENANT,
        trained_tier_name=result.trained_tier_name,
        harness_config=harness,
    )

    before = _recompute_pipeline_cost(_COST_BEFORE)
    after = _recompute_pipeline_cost(_COST_AFTER)
    delta_pct = (before - after) / before * 100 if before else 0.0

    return {
        "samples": result.samples_used,
        "jsonl_path": str(result.jsonl_path),
        "trained_tier": result.trained_tier_name,
        "harness_tiers": harness[TENANT],
        "cost_before": before,
        "cost_after": after,
        "delta_pct": delta_pct,
    }


# ── main ─────────────────────────────────────────────────────────────


async def main() -> None:
    print("=" * 60)
    print("  Dark Factory 1 — App Factory (tenant: app-factory)")
    print("=" * 60)
    print()

    # ── Multi-tenant bootstrap ────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="sagewai-factory-27-") as td:
        td_path = Path(td)
        state_path = td_path / "admin-state.json"
        artifacts_dir = td_path / "artifacts" / TENANT
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        bootstrap(state_path=state_path, run_preflight=False)
        print(f"admin state : {state_path}")
        print(f"artifacts   : {artifacts_dir}")
        print()

        # ── Shared fleet ──────────────────────────────────────────
        registry = InMemoryFleetRegistry()
        fleet_report = await seed_fleet(registry)
        print("Fleet seeded:")
        for tenant, names in fleet_report.items():
            label = "overflow" if tenant == "__overflow__" else tenant
            for name in names:
                print(f"  {label:<14} → {name}")
        print()

        workers = await registry.list_workers(org_id=ORG_ID)
        dedicated = next(
            w
            for w in workers
            if w.capabilities.labels.get("project_id") == TENANT
        )

        task_store = InMemoryTaskStore()
        dispatcher = FleetDispatcher(
            store=task_store,
            poll_timeout=1.0,
            poll_interval=0.1,
        )

        # Scoreboard knows which worker belongs to which tenant.
        dedicated_map: dict[str, str] = {}
        overflow_set: set[str] = set()
        for w in workers:
            pid = w.capabilities.labels.get("project_id")
            if pid:
                dedicated_map[w.name] = pid
            else:
                overflow_set.add(w.name)

        scoreboard = FleetScoreboard(
            dedicated_workers=dedicated_map,
            overflow_workers=overflow_set,
        )
        gate = ApprovalGate(TENANT, severity_threshold=3)

        # Fresh training store for this run.
        reset_training(TENANT)

        # ── Run the pipeline ──────────────────────────────────────
        brief = (
            "build a todo app with auth, postgres, and a react frontend"
        )
        models_for_tenant = list(dedicated.capabilities.models_supported)

        async with scoreboard:
            await run_app_factory(
                brief=brief,
                task_store=task_store,
                dispatcher=dispatcher,
                dedicated_worker_id=dedicated.id,
                dedicated_worker_name=dedicated.name,
                dedicated_pool=dedicated.capabilities.pool,
                dedicated_labels=dedicated.capabilities.labels,
                models=models_for_tenant,
                scoreboard=scoreboard,
                gate=gate,
                artifacts_dir=artifacts_dir,
                cost_table=_COST_BEFORE,
            )

        scoreboard.assert_isolated()
        await assert_cross_tenant_isolation(scoreboard)

        # ── Scoreboard ────────────────────────────────────────────
        print()
        print(scoreboard.render())

        # ── Training flywheel ─────────────────────────────────────
        print()
        print("Training flywheel:")
        tf = await run_training_flywheel(
            base_model=models_for_tenant[0],
            artifacts_dir=artifacts_dir,
        )
        print(
            f"  collected {tf['samples']} samples → exported → "
            f"stub-trained {tf['trained_tier']}"
        )
        print(f"  jsonl    : {tf['jsonl_path']}")
        print(f"  harness  : {' → '.join(tf['harness_tiers'])}")
        print(
            "  cost on full-pipeline re-run: "
            f"before=${tf['cost_before']:.4f}  "
            f"after=${tf['cost_after']:.4f}  "
            f"(-{tf['delta_pct']:.1f}%)"
        )

        # ── Artifact summary ──────────────────────────────────────
        print()
        print("Artifacts:")
        for path in sorted(artifacts_dir.rglob("*")):
            if path.is_file():
                print(f"  {path.relative_to(artifacts_dir)}")

        print()
        print("Dark Factory 1 complete.")


if __name__ == "__main__":
    asyncio.run(main())
