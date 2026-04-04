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
"""Example 20 — Distributed agents across any infrastructure.

Demonstrates the Fleet module for distributed worker deployment.
Workers register with the fleet registry, declare their model
capabilities and pool labels, then claim and execute tasks via
the dispatcher.

**Architecture**:

- FleetRegistry: tracks workers, enrollment keys, approval flow
- FleetDispatcher: long-poll task queue, model-aware matching
- Workers: register, heartbeat, claim tasks, report results
- Enrollment keys: single-use secrets for auto-approval

Requirements::

    pip install sagewai

Usage::

    python 20_fleet_workers.py
"""

from __future__ import annotations

import asyncio

from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerApprovalStatus,
    WorkerCapabilities,
)


async def main() -> None:
    """Demonstrate fleet worker registration and task dispatch."""
    print("=" * 60)
    print("  Fleet Workers — Distributed Agent Infrastructure")
    print("=" * 60)
    print()

    registry = InMemoryFleetRegistry()
    task_store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=task_store, poll_timeout=2.0)

    # ── Step 1: Create enrollment key ───────────────────────────────
    print("Step 1: Creating enrollment key for GPU cluster...")
    key_record, raw_key = await registry.create_enrollment_key(
        org_id="acme-corp",
        name="gpu-cluster-key",
        created_by="admin",
        max_uses=10,
        allowed_pools=["gpu"],
        allowed_models=["gpt-4o", "claude-sonnet-4-5-20250929"],
    )
    print(f"  Key ID: {key_record.id}")
    print(f"  Max uses: {key_record.max_uses}")
    print(f"  Allowed pools: {key_record.allowed_pools}")
    print()

    # ── Step 2: Register workers ────────────────────────────────────
    print("Step 2: Registering workers...")

    # GPU worker with enrollment key (auto-approved)
    gpu_worker = await registry.register_worker(
        name="worker-gpu-01",
        org_id="acme-corp",
        capabilities=WorkerCapabilities(
            pool="gpu",
            labels={"region": "us-east-1", "gpu": "a100"},
            models_supported=["gpt-4o", "claude-sonnet-4-5-20250929"],
            max_concurrent=4,
        ),
        enrollment_key=raw_key,
    )
    print(f"  {gpu_worker.name}: {gpu_worker.approval_status.value}")
    assert gpu_worker.approval_status == WorkerApprovalStatus.APPROVED

    # CPU worker without key (needs manual approval)
    cpu_worker = await registry.register_worker(
        name="worker-cpu-01",
        org_id="acme-corp",
        capabilities=WorkerCapabilities(
            pool="default",
            labels={"region": "eu-west-1"},
            models_supported=["gpt-4o-mini", "claude-haiku-4-5-20251001"],
            max_concurrent=8,
        ),
    )
    print(f"  {cpu_worker.name}: {cpu_worker.approval_status.value}")
    assert cpu_worker.approval_status == WorkerApprovalStatus.PENDING

    # Approve the CPU worker manually
    cpu_worker = await registry.approve_worker(cpu_worker.id, approved_by="admin")
    print(f"  {cpu_worker.name}: approved by admin")
    print()

    # ── Step 3: List fleet status ───────────────────────────────────
    print("Step 3: Fleet status...")
    workers = await registry.list_workers("acme-corp")
    for w in workers:
        caps = w.capabilities
        print(f"  {w.name}")
        print(f"    Pool: {caps.pool}, Models: {caps.models_supported}")
        print(f"    Labels: {caps.labels}")
        print(f"    Status: {w.approval_status.value}")
    print()

    # ── Step 4: Dispatch tasks ──────────────────────────────────────
    print("Step 4: Dispatching tasks...")

    # Enqueue tasks with model and pool requirements
    task_store.enqueue({
        "run_id": "run-001",
        "model": "gpt-4o",
        "pool": "gpu",
        "payload": "Analyze quarterly earnings report",
    })
    task_store.enqueue({
        "run_id": "run-002",
        "model": "gpt-4o-mini",
        "pool": "default",
        "payload": "Summarize meeting notes",
    })
    print("  Enqueued: run-001 (gpt-4o, gpu pool)")
    print("  Enqueued: run-002 (gpt-4o-mini, default pool)")

    # GPU worker claims matching task
    task = await dispatcher.claim(
        worker_id=gpu_worker.id,
        org_id="acme-corp",
        models_canonical=["gpt-4o", "claude-sonnet-4-5-20250929"],
        pool="gpu",
        labels={"region": "us-east-1"},
    )
    if task:
        print(f"  GPU worker claimed: {task['run_id']}")
        await dispatcher.report(
            worker_id=gpu_worker.id,
            org_id="acme-corp",
            run_id=task["run_id"],
            status="completed",
            output="Analysis complete: revenue up 12% YoY",
        )
        print(f"  GPU worker reported: {task['run_id']} completed")

    # CPU worker claims its matching task
    task = await dispatcher.claim(
        worker_id=cpu_worker.id,
        org_id="acme-corp",
        models_canonical=["gpt-4o-mini", "claude-haiku-4-5-20251001"],
        pool="default",
    )
    if task:
        print(f"  CPU worker claimed: {task['run_id']}")
        await dispatcher.report(
            worker_id=cpu_worker.id,
            org_id="acme-corp",
            run_id=task["run_id"],
            status="completed",
            output="Meeting summary: 3 action items identified",
        )
        print(f"  CPU worker reported: {task['run_id']} completed")
    print()

    # ── Step 5: Heartbeat ───────────────────────────────────────────
    print("Step 5: Worker heartbeats...")
    await registry.heartbeat(gpu_worker.id)
    await registry.heartbeat(cpu_worker.id)
    print("  All workers reported healthy")
    print()

    print("=" * 60)
    print("  Fleet is operational: 2 workers, 2 tasks completed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
