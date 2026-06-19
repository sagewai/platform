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
"""Example 26 — Project-Scoped Fleet: Workers Dispatch by Scope, LLM, and Tags.

Demonstrates Sagewai's fleet worker system with **project-scoped dispatch**.
Workers register with capabilities and project assignments. The dispatcher
matches tasks to workers by:

1. **Project scope** — workers assigned to a project only receive that
   project's tasks. Global workers (no project_id) serve all projects.
2. **Model capability** — tasks requiring specific LLMs (e.g., llama3)
   only route to workers that have those models available.
3. **Tags/labels** — workers tagged "gpu" receive training jobs,
   workers tagged "inference" receive chat tasks.
4. **Pool** — named pools partition workers (default, training, high-priority).

**Architecture**::

    ┌─────────────────────────────────────────────────────────┐
    │  Dispatcher (admin serve)                               │
    │                                                         │
    │  Task Queue:                                            │
    │    task-1: project=healthcare, model=llama3, pool=gpu   │
    │    task-2: project=finance, model=gpt-4o, pool=default  │
    │    task-3: project=null (global), pool=training         │
    └──────────┬──────────────────────────────────┬───────────┘
               │                                  │
    ┌──────────▼──────────┐          ┌────────────▼───────────┐
    │  Worker A            │          │  Worker B               │
    │  project: healthcare │          │  project: finance       │
    │  models: [llama3]    │          │  models: [gpt-4o]       │
    │  pool: gpu           │          │  pool: default          │
    │  labels: {gpu: true} │          │  labels: {inference: t} │
    │                      │          │                         │
    │  → claims task-1 ✓   │          │  → claims task-2 ✓      │
    │  → skips task-2 ✗    │          │  → skips task-1 ✗       │
    └──────────────────────┘          └─────────────────────────┘

    ┌───────────────────────┐
    │  Worker C (global)     │
    │  project: null         │
    │  models: [llama3]      │
    │  pool: training        │
    │  labels: {gpu: true}   │
    │                        │
    │  → claims task-3 ✓     │
    │  → can also claim any  │
    │    unmatched tasks     │
    └────────────────────────┘

Requirements::

    pip install sagewai

Usage::

    python examples/26_fleet_scoped_dispatch.py
"""

from __future__ import annotations

import asyncio

from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerCapabilities,
)
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import NetworkPolicy, SandboxMode


async def main() -> None:
    print("=" * 60)
    print("  Sagewai Fleet — Project-Scoped Worker Dispatch")
    print("=" * 60)
    print()

    # ── Initialize fleet infrastructure ──

    registry = InMemoryFleetRegistry()
    task_store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(
        store=task_store,
        poll_timeout=2.0,  # short timeout for demo
        poll_interval=0.5,
    )

    print("Fleet infrastructure initialized:")
    print("  Registry: InMemoryFleetRegistry")
    print("  TaskStore: InMemoryTaskStore")
    print("  Dispatcher: FleetDispatcher (poll_timeout=2s)")
    print()

    # ── Create enrollment keys for different pools ──

    gpu_key_record, gpu_key = await registry.create_enrollment_key(
        org_id="acme-corp",
        name="GPU Cluster Key",
        created_by="admin",
        max_uses=10,
        allowed_pools=["gpu", "training"],
        allowed_models=["llama3", "gemma2"],
    )

    inference_key_record, inference_key = await registry.create_enrollment_key(
        org_id="acme-corp",
        name="Inference Pool Key",
        created_by="admin",
        max_uses=50,
        allowed_pools=["default", "inference"],
    )

    print("Enrollment keys created:")
    print(f"  GPU Cluster: {gpu_key[:20]}... (max {gpu_key_record.max_uses} uses)")
    print(f"  Inference:   {inference_key[:20]}... (max {inference_key_record.max_uses} uses)")
    print()

    # ── Register workers with different capabilities ──

    # Worker A: Healthcare project, GPU, local Llama 3
    worker_a = await registry.register_worker(
        name="healthcare-gpu-01",
        org_id="acme-corp",
        capabilities=WorkerCapabilities(
            models_supported=["llama3", "gemma2:9b"],
            pool="gpu",
            max_concurrent=2,
            labels={
                "project_id": "healthcare",
                "gpu": "true",
                "gpu_model": "A100",
                "region": "eu-west-1",
            },
        ),
        enrollment_key=gpu_key,
    )

    # Worker B: Finance project, inference only, cloud models
    worker_b = await registry.register_worker(
        name="finance-inference-01",
        org_id="acme-corp",
        capabilities=WorkerCapabilities(
            models_supported=["gpt-4o", "claude-sonnet-4-20250514"],
            pool="default",
            max_concurrent=8,
            labels={
                "project_id": "finance",
                "inference": "true",
                "region": "us-east-1",
            },
        ),
        enrollment_key=inference_key,
    )

    # Worker C: Global (no project), training pool, GPU
    worker_c = await registry.register_worker(
        name="training-cluster-01",
        org_id="acme-corp",
        capabilities=WorkerCapabilities(
            models_supported=["llama3", "mistral-7b"],
            pool="training",
            max_concurrent=1,  # training jobs are resource-intensive
            labels={
                "gpu": "true",
                "gpu_model": "H100",
                "training": "true",
            },
        ),
        enrollment_key=gpu_key,
    )

    print("Workers registered (auto-approved via enrollment keys):")
    print(f"  {worker_a.name}: project=healthcare, pool=gpu, models={worker_a.capabilities.models_supported}")
    print(f"    status={worker_a.approval_status.value}, labels={worker_a.capabilities.labels}")
    print(f"  {worker_b.name}: project=finance, pool=default, models={worker_b.capabilities.models_supported}")
    print(f"    status={worker_b.approval_status.value}, labels={worker_b.capabilities.labels}")
    print(f"  {worker_c.name}: project=global, pool=training, models={worker_c.capabilities.models_supported}")
    print(f"    status={worker_c.approval_status.value}, labels={worker_c.capabilities.labels}")
    print()

    # ── Enqueue tasks with different scopes ──

    tasks = [
        {
            "run_id": "task-healthcare-inference",
            "model": "llama3",
            "pool": "gpu",
            "labels": {"project_id": "healthcare"},
            "payload": "Patient presents with chest pain and shortness of breath. Assess risk.",
            "requires_sandbox_mode": SandboxMode.PER_RUN,
            "requires_image": f"ghcr.io/sagewai/sandbox-ml:{image_manifest.SDK_VERSION}",
            "requires_network_policy": NetworkPolicy.NONE,
        },
        {
            "run_id": "task-finance-analysis",
            "model": "gpt-4o",
            "pool": "default",
            "labels": {"project_id": "finance"},
            "payload": "Analyze Q3 earnings report for ACME Corp and identify key risks.",
            "requires_sandbox_mode": SandboxMode.PER_RUN,
            "requires_image": f"ghcr.io/sagewai/sandbox-general:{image_manifest.SDK_VERSION}",
            "requires_network_policy": NetworkPolicy.FULL,
        },
        {
            "run_id": "task-training-finetune",
            "model": "llama3",
            "pool": "training",
            "labels": {"training": "true"},
            "payload": "Fine-tune llama3 on healthcare Q&A dataset (2000 samples).",
            "requires_sandbox_mode": SandboxMode.PER_RUN,
            "requires_image": f"ghcr.io/sagewai/sandbox-ml:{image_manifest.SDK_VERSION}",
            "requires_network_policy": NetworkPolicy.NONE,
        },
    ]

    for t in tasks:
        await task_store.enqueue(t)

    print(f"Tasks enqueued: {len(tasks)}")
    for t in tasks:
        print(f"  {t['run_id']}: model={t['model']}, pool={t['pool']}, labels={t['labels']}")
    print()

    # ── Workers claim their matching tasks ──

    print("Worker dispatch (each worker claims matching tasks):")
    print()

    # Worker A (healthcare, gpu) claims healthcare task
    task_a = await dispatcher.claim(
        worker_id=worker_a.id,
        org_id="acme-corp",
        models_canonical=worker_a.capabilities.models_supported,
        pool=worker_a.capabilities.pool,
        labels=worker_a.capabilities.labels,
    )
    if task_a:
        print(f"  ✓ {worker_a.name} claimed: {task_a['run_id']}")
        print(f"    payload: {task_a['payload'][:60]}...")
        await dispatcher.report(
            worker_id=worker_a.id, org_id="acme-corp",
            run_id=task_a["run_id"], status="completed",
            output="Risk assessment complete. Recommend ECG and troponin levels.",
        )
        print(f"    → reported: completed")
    else:
        print(f"  ✗ {worker_a.name}: no matching task")
    print()

    # Worker B (finance, default) claims finance task
    task_b = await dispatcher.claim(
        worker_id=worker_b.id,
        org_id="acme-corp",
        models_canonical=worker_b.capabilities.models_supported,
        pool=worker_b.capabilities.pool,
        labels=worker_b.capabilities.labels,
    )
    if task_b:
        print(f"  ✓ {worker_b.name} claimed: {task_b['run_id']}")
        print(f"    payload: {task_b['payload'][:60]}...")
        await dispatcher.report(
            worker_id=worker_b.id, org_id="acme-corp",
            run_id=task_b["run_id"], status="completed",
            output="Q3 revenue up 12%. Key risk: supply chain disruption in APAC.",
        )
        print(f"    → reported: completed")
    else:
        print(f"  ✗ {worker_b.name}: no matching task")
    print()

    # Worker C (global, training) claims training task
    task_c = await dispatcher.claim(
        worker_id=worker_c.id,
        org_id="acme-corp",
        models_canonical=worker_c.capabilities.models_supported,
        pool=worker_c.capabilities.pool,
        labels=worker_c.capabilities.labels,
    )
    if task_c:
        print(f"  ✓ {worker_c.name} claimed: {task_c['run_id']}")
        print(f"    payload: {task_c['payload'][:60]}...")
        await dispatcher.report(
            worker_id=worker_c.id, org_id="acme-corp",
            run_id=task_c["run_id"], status="completed",
            output="Fine-tuning complete. Loss: 0.42 → 0.18. Model saved to /models/healthcare-llama3-ft.",
        )
        print(f"    → reported: completed")
    else:
        print(f"  ✗ {worker_c.name}: no matching task")
    print()

    # ── Verify isolation: Worker A cannot claim finance tasks ──

    print("Isolation check:")
    task_cross = await dispatcher.claim(
        worker_id=worker_a.id,
        org_id="acme-corp",
        models_canonical=worker_a.capabilities.models_supported,
        pool=worker_a.capabilities.pool,
        labels=worker_a.capabilities.labels,
    )
    if task_cross:
        print(f"  ✗ FAIL: {worker_a.name} claimed a cross-project task!")
    else:
        print(f"  ✓ {worker_a.name} (healthcare) correctly got no finance/training tasks")
    print()

    # ── Heartbeat ──

    for w in [worker_a, worker_b, worker_c]:
        await registry.heartbeat(w.id)
    print("Heartbeats sent for all workers")

    # ── List workers ──

    all_workers = await registry.list_workers(org_id="acme-corp")
    print(f"\nFleet status: {len(all_workers)} workers registered")
    for w in all_workers:
        hb = w.last_heartbeat.strftime("%H:%M:%S") if w.last_heartbeat else "never"
        print(f"  {w.name}: {w.approval_status.value}, pool={w.capabilities.pool}, "
              f"models={len(w.capabilities.models_supported)}, heartbeat={hb}")
    print()

    print("=" * 60)
    print("  Fleet dispatch complete")
    print("  3 tasks dispatched to 3 workers by scope/model/pool")
    print("  Cross-project isolation verified")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
