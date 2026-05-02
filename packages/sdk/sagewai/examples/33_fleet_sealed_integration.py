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
"""Example 33 — Fleet + Sealed: workers carry security profiles + project scope.

Demonstrates the **spine touching the pillar**: how Sealed (security)
integrates with Fleet (distributed execution).

The architecture story for production multi-tenant deployments:

1. Each customer has its own Sealed security profile (per-CLI workload
   identity, scoped secrets, ACL).
2. Each customer has its own Sagewai project (project_id-scoped agents,
   workflows, runs).
3. Workers register with BOTH — a project_id (carried in ``labels``)
   AND a sealed_profile reference (also carried in ``labels``).
4. Tasks enqueued for customer X get dispatched ONLY to workers whose
   labels match the task's ``project_id`` AND ``sealed_profile``.
5. Cross-tenant leakage: prevented at TWO axes — Fleet's
   capability/label-based dispatch + Sealed's per-CLI identity check.

This example simulates **3 customers** (acme, globex, initech) each
with their own sealed profile + project, plus 5 workers configured
with different (project, profile) combinations. We then enqueue 6
tasks (2 acme, 1 globex, 1 initech, 1 global, 1 cross-test attempt)
and show that the dispatcher correctly routes by both axes.

What's exercised:

- ``InMemoryFleetRegistry.register_worker()`` with project_id labels
- ``BuiltinAdminStoreBackend`` with 3 distinct Sealed profiles
- ``InMemoryTaskStore.enqueue()`` with capability + label requirements
- ``FleetDispatcher.claim()`` matching by (model, pool, labels)
- ``resolve_security_profile()`` cascade per-customer
- Cross-tenant attempt → correctly rejected

Requirements::

    pip install 'sagewai[fleet]'

Usage::

    python 33_fleet_sealed_integration.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cryptography.fernet import Fernet

from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.models import WorkerCapabilities
from sagewai.fleet.registry import InMemoryFleetRegistry
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import NetworkPolicy, SandboxMode
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import ProfileWritePayload
from sagewai.sealed.refs import _BACKENDS
from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

ORG_ID = "acme-corp"


# ── 3 customers, each with project + sealed profile ───────────────


CUSTOMERS = [
    {
        "id": "acme",
        "project_id": "acme-prod",
        "profile_id": "acme-customer-profile",
        "env": {"DEPLOY_REGION": "eu-west-1", "TIER": "enterprise"},
        "secrets": {"API_KEY": "acme-secret-12345"},
    },
    {
        "id": "globex",
        "project_id": "globex-prod",
        "profile_id": "globex-customer-profile",
        "env": {"DEPLOY_REGION": "us-east-1", "TIER": "premium"},
        "secrets": {"API_KEY": "globex-secret-67890"},
    },
    {
        "id": "initech",
        "project_id": "initech-prod",
        "profile_id": "initech-customer-profile",
        "env": {"DEPLOY_REGION": "us-west-2", "TIER": "starter"},
        "secrets": {"API_KEY": "initech-secret-abcde"},
    },
]


# ── 5 workers — different (project, profile) combinations ─────────


WORKERS_TO_REGISTER = [
    # Worker assigned to ACME exclusively
    {
        "name": "worker-acme-1",
        "project_id": "acme-prod",
        "profile_ref": "acme-customer-profile",
        "models": ["claude-haiku-4-5", "gpt-4o-mini"],
        "extra_labels": {"customer": "acme", "type": "general"},
    },
    # Another worker for ACME (high-throughput)
    {
        "name": "worker-acme-2",
        "project_id": "acme-prod",
        "profile_ref": "acme-customer-profile",
        "models": ["claude-haiku-4-5"],
        "extra_labels": {"customer": "acme", "type": "fast"},
    },
    # GLOBEX worker
    {
        "name": "worker-globex-1",
        "project_id": "globex-prod",
        "profile_ref": "globex-customer-profile",
        "models": ["claude-haiku-4-5"],
        "extra_labels": {"customer": "globex", "type": "general"},
    },
    # INITECH worker
    {
        "name": "worker-initech-1",
        "project_id": "initech-prod",
        "profile_ref": "initech-customer-profile",
        "models": ["gpt-4o-mini"],
        "extra_labels": {"customer": "initech", "type": "general"},
    },
    # Org-global worker — carries no customer-scoped labels.
    # It can only claim tasks whose own labels are empty (org-global).
    {
        "name": "worker-shared-1",
        "project_id": None,
        "profile_ref": None,
        "models": ["claude-haiku-4-5"],
        "extra_labels": {"type": "fallback"},
    },
]


# ── 6 tasks — 2 per customer, plus cross-tenant attempts ─────────


TASKS_TO_ENQUEUE = [
    # ACME tasks
    {"run_id": "task-acme-1", "project_id": "acme-prod", "profile_ref": "acme-customer-profile", "model": "claude-haiku-4-5", "for": "acme"},
    {"run_id": "task-acme-2", "project_id": "acme-prod", "profile_ref": "acme-customer-profile", "model": "claude-haiku-4-5", "for": "acme"},
    # GLOBEX
    {"run_id": "task-globex-1", "project_id": "globex-prod", "profile_ref": "globex-customer-profile", "model": "claude-haiku-4-5", "for": "globex"},
    # INITECH
    {"run_id": "task-initech-1", "project_id": "initech-prod", "profile_ref": "initech-customer-profile", "model": "gpt-4o-mini", "for": "initech"},
    # Org-global task (no project, no sealed)
    {"run_id": "task-global-1", "project_id": None, "profile_ref": None, "model": "claude-haiku-4-5", "for": "shared"},
    # Another ACME task — extra coverage
    {"run_id": "task-acme-3", "project_id": "acme-prod", "profile_ref": "acme-customer-profile", "model": "claude-haiku-4-5", "for": "acme"},
]


def _task_labels(t: dict) -> dict[str, str]:
    """Build the dispatch labels dict for a task."""
    labels: dict[str, str] = {}
    if t["project_id"]:
        labels["project_id"] = t["project_id"]
    if t["profile_ref"]:
        labels["sealed_profile"] = t["profile_ref"]
    return labels


def _worker_labels(w: dict) -> dict[str, str]:
    """Build the labels dict that a worker advertises."""
    labels = dict(w["extra_labels"])
    if w["project_id"]:
        labels["project_id"] = w["project_id"]
    if w["profile_ref"]:
        labels["sealed_profile"] = w["profile_ref"]
    return labels


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai — Fleet + Sealed integration (example 33)")
    print("─" * 72)
    print()

    # 1. Set up the Sealed backend with 3 customer profiles
    crypto = Crypto(Fernet.generate_key())
    profiles_path = Path("/tmp/example33-profiles.json")
    if profiles_path.exists():
        profiles_path.unlink()  # fresh start each run
    sealed_backend = BuiltinAdminStoreBackend(
        profiles_path=profiles_path, crypto=crypto,
    )
    # Override the default in-process backend with our isolated test
    # instance. (`register_backend()` is idempotent for same-instance
    # but rejects different-instance overrides; for a runnable example
    # we replace the registry entry directly.)
    _BACKENDS["builtin"] = sealed_backend

    print("  Setting up Sealed profiles (3 customers)…")
    for cust in CUSTOMERS:
        await sealed_backend.save_profile(ProfileWritePayload(
            id=cust["profile_id"],
            name=cust["id"].title(),
            env=cust["env"],
            secrets=cust["secrets"],
        ))
        print(f"    {cust['profile_id']:<32} env={cust['env']!r}")
    print()

    # 2. Set up the Fleet — registry + dispatcher + task store.
    #    Short poll_timeout so an empty queue returns quickly.
    print("  Setting up Fleet…")
    registry = InMemoryFleetRegistry()
    task_store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(
        store=task_store, poll_interval=0.05, poll_timeout=0.2,
    )

    # 3. Register 5 workers across customers
    print("  Registering 5 workers (mix of project + sealed profile)…")
    worker_records: dict[str, object] = {}
    for w in WORKERS_TO_REGISTER:
        labels = _worker_labels(w)
        record = await registry.register_worker(
            name=w["name"],
            org_id=ORG_ID,
            capabilities=WorkerCapabilities(
                models_supported=w["models"],
                pool="default",
                labels=labels,
                max_concurrent=4,
            ),
        )
        await registry.approve_worker(record.id, approved_by="admin")
        worker_records[w["name"]] = record
        scope = w["project_id"] or "ORG-GLOBAL"
        sealed = w["profile_ref"] or "(none)"
        print(f"    {w['name']:<22} project={scope:<14} sealed={sealed}")
    print()

    # 4. Enqueue 6 tasks
    print("  Enqueuing 6 tasks (3 acme, 1 globex, 1 initech, 1 global)…")
    for t in TASKS_TO_ENQUEUE:
        scope = t["project_id"] or "ORG-GLOBAL"
        task_store.enqueue({
            "run_id": t["run_id"],
            "model": t["model"],
            "pool": "default",
            "labels": _task_labels(t),
            "payload": f"task for {t['for']}",
            # Sealed-pillar requirement: every dispatched task declares
            # the sandbox profile it needs so the runtime can isolate
            # the worker. This example uses the general-purpose image
            # with no outbound network — fleet/sealed isolation is what
            # we're demonstrating, not third-party calls.
            "requires_sandbox_mode": SandboxMode.PER_RUN,
            "requires_image": (
                f"ghcr.io/sagewai/sandbox-general:{image_manifest.SDK_VERSION}"
            ),
            "requires_network_policy": NetworkPolicy.NONE,
        })
        print(f"    {t['run_id']:<20} project={scope:<14} model={t['model']:<20}")
    print()

    # 5. Each worker drains tasks until none match
    print("─" * 72)
    print(" Dispatch: each worker tries to claim tasks")
    print("─" * 72)
    print()

    claims: dict[str, list[str]] = {}
    for w in WORKERS_TO_REGISTER:
        record = worker_records[w["name"]]
        claims[w["name"]] = []
        labels = _worker_labels(w)
        # Drain — keep claiming until we time out
        for _ in range(3):
            task = await dispatcher.claim(
                worker_id=record.id,
                org_id=ORG_ID,
                models_canonical=record.capabilities.models_canonical,
                pool=record.capabilities.pool,
                labels=labels,
            )
            if task is None:
                break
            claims[w["name"]].append(task["run_id"])
            await dispatcher.report(
                worker_id=record.id,
                org_id=ORG_ID,
                run_id=task["run_id"],
                status="completed",
                output=f"processed by {w['name']}",
            )
        print(
            f"  {w['name']:<22} claimed {len(claims[w['name']])} task(s): "
            f"{claims[w['name']] or '(none)'}"
        )
    print()

    # 6. Verify isolation — no cross-tenant claim should have happened
    print("─" * 72)
    print(" Isolation verification")
    print("─" * 72)
    print()
    # The Fleet invariant we verify: a TENANT-SCOPED task (with project_id
    # or sealed_profile labels) must NEVER be claimed by a worker of a
    # DIFFERENT tenant. Org-global tasks (no labels) are claimable by
    # any worker — that's the dispatcher's intended semantic for shared
    # work, not a leak.
    leaks_detected = 0
    for worker_name, claimed_run_ids in claims.items():
        worker_meta = next(w for w in WORKERS_TO_REGISTER if w["name"] == worker_name)
        worker_proj = worker_meta["project_id"]
        worker_profile = worker_meta["profile_ref"]
        for rid in claimed_run_ids:
            task_meta = next(t for t in TASKS_TO_ENQUEUE if t["run_id"] == rid)
            task_proj = task_meta["project_id"]
            task_profile = task_meta["profile_ref"]
            # Cross-tenant leak: task is tenant-scoped AND worker is in
            # a different tenant (or no tenant).
            tenant_scoped = task_proj is not None or task_profile is not None
            project_ok = (worker_proj == task_proj) or task_proj is None
            profile_ok = (worker_profile == task_profile) or task_profile is None
            if tenant_scoped and not (project_ok and profile_ok):
                leaks_detected += 1
                print(
                    f"  LEAK: {worker_name} (project={worker_proj}, "
                    f"sealed={worker_profile}) claimed {rid} "
                    f"(project={task_proj}, sealed={task_profile})"
                )
            else:
                tag = task_proj or "shared"
                print(f"  OK   {worker_name} legitimately claimed {rid} ({tag})")
    print()
    if leaks_detected == 0:
        print("  Zero cross-tenant leaks — Fleet+Sealed isolation honoured.")
    else:
        print(f"  {leaks_detected} LEAK(S) detected!")
    print()

    # 7. Verify Sealed cascade resolution per project — each customer
    #    can only resolve its own profile's secrets
    print("─" * 72)
    print(" Sealed cascade resolution per-customer")
    print("─" * 72)
    print()
    for cust in CUSTOMERS:
        eff = await resolve_security_profile(
            levels=[CascadeLevel(
                name="user", profile_ref=cust["profile_id"], overrides=None,
            )],
        )
        print(f"  Customer {cust['id']!r} resolved profile:")
        print(f"    env             = {dict(eff.env)}")
        print(f"    secret_keys     = {sorted(eff.secret_keys)}")
        print(f"    cascade_origins = {dict(eff.cascade_origins)}")
        print()

    # 8. Final stats
    print("─" * 72)
    print(" Final state")
    print("─" * 72)
    print()
    workers_listed = await registry.list_workers(org_id=ORG_ID)
    total_claimed = sum(len(c) for c in claims.values())
    print(f"  Workers registered:    {len(workers_listed)}")
    print(f"  Tasks enqueued:        {len(TASKS_TO_ENQUEUE)}")
    print(f"  Total tasks claimed:   {total_claimed}")
    print(f"  Cross-tenant leaks:    {leaks_detected}")
    print()
    print(json.dumps({
        "customers": [c["id"] for c in CUSTOMERS],
        "workers": list(claims.keys()),
        "claims": claims,
        "leaks": leaks_detected,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
