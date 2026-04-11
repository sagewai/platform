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
"""Example 21 — The complete Sagewai stack in one example.

Combines all five pillars of Sagewai in a single runnable script:

1. **Agents** — UniversalAgent with tool calling
2. **Training** — Harness discovery for local/fine-tuned models
3. **Fleet** — Distributed worker registration and dispatch
4. **Harness** — Smart routing with complexity-based tiers
5. **Observatory** — Cost tracking and audit logging

Shows how these modules compose to form a complete enterprise
AI platform with governance, cost control, and observability.

Requirements::

    pip install sagewai[harness]

Usage::

    python 21_full_stack.py
"""

from __future__ import annotations

import asyncio
import os

from sagewai.engines.universal import UniversalAgent
from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerCapabilities,
)
from sagewai.harness.discovery import discover_local_backends
from sagewai.harness.models import (
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
)
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.observability.audit import AuditEvent, AuditLogger, InMemoryAuditBackend
from sagewai.observability.costs import CostTracker


async def main() -> None:
    """Demonstrate the complete Sagewai stack."""
    print("=" * 60)
    print("  The Complete Sagewai Stack")
    print("=" * 60)
    print()

    # ── Pillar 1: Observatory (Cost Tracking + Audit) ───────────────
    print("--- Pillar 1: Observatory ---")
    tracker = CostTracker()
    audit_backend = InMemoryAuditBackend()
    audit = AuditLogger(backends=[audit_backend])

    audit.log(AuditEvent(
        action="system_startup",
        agent_name="full-stack-demo",
        project_id="demo-project",
    ))
    print("  CostTracker initialized")
    print("  AuditLogger initialized")
    print()

    # ── Pillar 2: Training — Local Model Discovery ──────────────────
    print("--- Pillar 2: Training (Local Model Discovery) ---")
    discovered = await discover_local_backends()

    local_model = "ollama/llama3.1:8b"
    if discovered:
        for name, server in discovered.items():
            print(f"  Discovered {name}: {', '.join(server.models[:3])}")
            local_model = f"{name}/{server.models[0]}"
    else:
        print("  No local servers (using cloud-only config)")
    print()

    # ── Pillar 3: Harness — Smart Routing ───────────────────────────
    print("--- Pillar 3: Harness (Smart Routing) ---")
    tier_config = ModelTierConfig(
        simple=local_model if discovered else "claude-haiku-4-5-20251001",
        medium="claude-sonnet-4-5-20250929",
        complex="claude-opus-4-6",
    )

    harness_store = InMemoryHarnessStore()
    dev_key = HarnessKey(
        name="demo-key",
        user_id="developer",
        org_id="demo-org",
        max_budget_daily_usd=25.00,
        max_budget_monthly_usd=500.00,
    )
    api_key = await harness_store.create_key(dev_key)

    await harness_store.create_policy(PolicyRule(
        name="cost-optimized",
        description="Route by complexity, local models for simple tasks",
        scope=PolicyScope(org_id="demo-org"),
        allow_override=True,
    ))

    print(f"  SIMPLE  -> {tier_config.simple}")
    print(f"  MEDIUM  -> {tier_config.medium}")
    print(f"  COMPLEX -> {tier_config.complex}")
    print(f"  API key: ...{api_key[-8:]}")
    print(f"  Budget:  $25/day, $500/month")
    print()

    # ── Pillar 4: Fleet — Distributed Workers ───────────────────────
    print("--- Pillar 4: Fleet (Distributed Workers) ---")
    registry = InMemoryFleetRegistry()
    task_store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=task_store, poll_timeout=2.0)

    # Create enrollment key and register a worker
    _, enrollment_key = await registry.create_enrollment_key(
        org_id="demo-org",
        name="demo-cluster",
        created_by="admin",
    )

    worker = await registry.register_worker(
        name="worker-01",
        org_id="demo-org",
        capabilities=WorkerCapabilities(
            pool="default",
            models_supported=["gpt-4o", "gpt-4o-mini"],
            max_concurrent=4,
        ),
        enrollment_key=enrollment_key,
    )
    print(f"  Worker: {worker.name} ({worker.approval_status.value})")
    print(f"  Pool: {worker.capabilities.pool}")
    print(f"  Models: {worker.capabilities.models_supported}")
    print()

    # ── Pillar 5: Agents — Run a Task ───────────────────────────────
    print("--- Pillar 5: Agents (Task Execution) ---")
    agent = UniversalAgent(
        name="full-stack-agent",
        model=os.getenv("SAGEWAI_MODEL", "gpt-4o-mini"),
        system_prompt="You are a helpful assistant. Be concise.",
    )
    agent.on_event(tracker.event_hook)
    agent.on_event(audit.create_event_hook(project_id="demo-project"))

    # Dispatch via fleet
    task_store.enqueue({
        "run_id": "demo-run-001",
        "model": "gpt-4o-mini",
        "pool": "default",
        "payload": "What are the benefits of agentic AI?",
    })

    task = await dispatcher.claim(
        worker_id=worker.id,
        org_id="demo-org",
        models_canonical=["gpt-4o", "gpt-4o-mini"],
        pool="default",
    )

    if task:
        print(f"  Worker claimed: {task['run_id']}")

        # Agent processes the task
        response = await agent.chat(task["payload"])
        print(f"  Agent response: {response[:80]}...")

        await dispatcher.report(
            worker_id=worker.id,
            org_id="demo-org",
            run_id=task["run_id"],
            status="completed",
            output=response,
        )
        print(f"  Task completed: {task['run_id']}")
    print()

    # ── Full Stack Summary ──────────────────────────────────────────
    print("=" * 60)
    print("  Full Stack Summary")
    print("=" * 60)
    print()
    print("  Observatory:")
    print(f"    Total cost:  ${tracker.total_cost:.4f}")
    await audit.flush()
    print(f"    Audit events: {len(audit_backend.events)}")
    print()
    print("  Harness:")
    print(f"    Routing tiers: 3 (simple/medium/complex)")
    print(f"    Local models:  {'yes' if discovered else 'no'}")
    print()
    print("  Fleet:")
    workers = await registry.list_workers("demo-org")
    print(f"    Workers: {len(workers)}")
    print(f"    Tasks completed: {len(task_store._completed)}")
    print()
    print("  All five pillars operational.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
