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
"""Example 40 — Fleet under load: 24 workers, 100 tasks, real numbers.

The Fleet pillar's headline claim is that workers route by capability +
project, with **zero cross-tenant pollution** even under concurrent
load. Examples 26 and 33 prove the routing model on small (3-5 worker)
synchronous flows. This example proves it under load: **24 workers
across 3 tenants, 100 tasks split across 2 pools, dispatched
concurrently**, with measured throughput and latency.

The runtime story:

1. Register 24 workers spread evenly across 3 tenants (acme, globex,
   initech), 2 pools (``default``, ``fast``), and a mix of model
   capabilities.
2. Enqueue 100 tasks with the same project + pool + model split.
3. Drain the queue concurrently — every worker calls
   :meth:`FleetDispatcher.claim` in its own asyncio task, simulates a
   short workload, then calls :meth:`FleetDispatcher.report` and loops.
4. Report tasks/sec, p50/p95/p99 dispatch latency, per-tenant claim
   distribution, and a hard pass/fail on the cross-tenant isolation
   invariant.

This is the load generator. The Iron Man HUD recording (issue
``sagewai/atelier#7``) wires the same dispatch pattern to a Postgres
``TaskStore`` so the admin UI's HUD page can observe the live state
— the in-memory store keeps this example dependency-free, which is
the right shape for a runnable demo.

What's exercised:

- ``InMemoryFleetRegistry.register_worker()`` at scale (24 workers)
- ``InMemoryTaskStore.enqueue()`` with mixed pool + model + label keys
- ``FleetDispatcher.claim()`` / ``.report()`` under concurrent
  ``asyncio.gather`` dispatch
- Capability matching by model + pool + project_id label
- Tenant-scoped isolation invariant: a tenant-scoped task is never
  claimed by a worker of a different tenant

Requirements::

    pip install 'sagewai[fleet]'

Usage::

    python 40_fleet_under_load.py
    python 40_fleet_under_load.py --task-ms 200   # slower work for HUD recording
    python 40_fleet_under_load.py --workers 32 --tasks 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.models import WorkerCapabilities
from sagewai.fleet.registry import InMemoryFleetRegistry
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import NetworkPolicy, SandboxMode

ORG_ID = "acme-corp"

# ── Tenants — the dispatch isolation axis ─────────────────────────

TENANTS = [
    {"id": "acme",    "project_id": "acme-prod",    "task_share": 40},
    {"id": "globex",  "project_id": "globex-prod",  "task_share": 35},
    {"id": "initech", "project_id": "initech-prod", "task_share": 25},
]

POOLS = ["default", "fast"]
MODELS_BY_INDEX = (
    ["claude-haiku-4-5"],
    ["claude-haiku-4-5", "gpt-4o-mini"],
    ["gpt-4o-mini"],
)


# ── Runtime stats ─────────────────────────────────────────────────


@dataclass
class WorkerStats:
    """Per-worker run stats accumulated during the dispatch drain."""

    name: str
    project_id: str
    claimed: list[str] = field(default_factory=list)
    claim_latencies_ms: list[float] = field(default_factory=list)
    last_claim_t: float = 0.0  # perf_counter() when last claim returned a task


# ── Builders ──────────────────────────────────────────────────────


def _build_workers(per_tenant: int) -> list[dict]:
    """Build ``per_tenant`` workers for each tenant.

    Mixes pool + model coverage so dispatch must do real matching, not
    just take the first worker off the list.
    """
    out: list[dict] = []
    for tenant in TENANTS:
        for i in range(per_tenant):
            pool = POOLS[i % len(POOLS)]
            models = MODELS_BY_INDEX[i % len(MODELS_BY_INDEX)]
            out.append({
                "name": f"{tenant['id']}-w{i:02d}",
                "project_id": tenant["project_id"],
                "pool": pool,
                "models": list(models),
            })
    return out


def _build_tasks(total: int) -> list[dict]:
    """Build ``total`` tasks distributed by tenant share.

    Every task is tenant-scoped (carries a ``project_id`` label) so the
    cross-tenant isolation check has full coverage.
    """
    shares = [t["task_share"] for t in TENANTS]
    share_total = sum(shares)
    counts = [round(total * s / share_total) for s in shares]
    # Compensate rounding so counts sum to exactly ``total``
    counts[0] += total - sum(counts)

    out: list[dict] = []
    for tenant, count in zip(TENANTS, counts):
        for i in range(count):
            pool = POOLS[i % len(POOLS)]
            # Models cycle with a different period than workers so the
            # match isn't trivial (worker[i] does not always claim task[i]).
            model = ("claude-haiku-4-5", "gpt-4o-mini")[i % 2]
            out.append({
                "run_id": f"{tenant['id']}-task-{i:03d}",
                "project_id": tenant["project_id"],
                "pool": pool,
                "model": model,
            })
    return out


# ── Dispatch ──────────────────────────────────────────────────────


async def _worker_loop(
    *,
    dispatcher: FleetDispatcher,
    worker_record,
    worker_meta: dict,
    task_simulated_ms: float,
    stats: WorkerStats,
) -> None:
    """Drain the queue from one worker's perspective.

    Loops ``claim`` → simulate work → ``report`` until ``claim`` times
    out (the queue is empty). Records per-claim wall latency for the
    p50/p95/p99 report.
    """
    labels = {"project_id": worker_meta["project_id"]}
    while True:
        t0 = time.perf_counter()
        task = await dispatcher.claim(
            worker_id=worker_record.id,
            org_id=ORG_ID,
            models_canonical=worker_record.capabilities.models_canonical,
            pool=worker_record.capabilities.pool,
            labels=labels,
        )
        if task is None:
            return
        t_claimed = time.perf_counter()
        stats.claim_latencies_ms.append((t_claimed - t0) * 1000.0)
        stats.last_claim_t = t_claimed
        if task_simulated_ms > 0:
            await asyncio.sleep(task_simulated_ms / 1000.0)
        stats.claimed.append(task["run_id"])
        await dispatcher.report(
            worker_id=worker_record.id,
            org_id=ORG_ID,
            run_id=task["run_id"],
            status="completed",
            output=f"processed by {worker_meta['name']}",
        )


# ── Reporting helpers ─────────────────────────────────────────────


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile. Returns 0 for empty input."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _bar(value: float, scale: float, width: int = 20) -> str:
    """Unicode bar of length proportional to ``value / scale``."""
    if scale <= 0:
        return "·" * width
    filled = max(0, min(width, round(width * value / scale)))
    return ("█" * filled) + ("·" * (width - filled))


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * (68 - len(text))}")


# ── main ──────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--workers", type=int, default=24,
        help="total worker count across the 3 tenants (default 24, must be ≥21)",
    )
    p.add_argument(
        "--tasks", type=int, default=100,
        help="total tasks to enqueue across the 3 tenants (default 100)",
    )
    p.add_argument(
        "--task-ms", type=float, default=0.0,
        help="simulated work duration per task in ms (default 0; "
             "set to 100-300 to stretch the run for the HUD recording)",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    if args.workers < 3 * len(TENANTS):
        raise SystemExit(
            f"--workers must be ≥ {3 * len(TENANTS)} so each tenant gets "
            f"a meaningful pool/model spread (got {args.workers})",
        )
    per_tenant = args.workers // len(TENANTS)
    actual_workers = per_tenant * len(TENANTS)

    _line()
    print(" Sagewai Fleet under load — 24 workers, 100 tasks, real numbers")
    _line()
    print()
    print(f"  tenants:           {len(TENANTS)}  ({', '.join(t['id'] for t in TENANTS)})")
    print(f"  workers:           {actual_workers}  ({per_tenant} per tenant)")
    print(f"  pools:             {len(POOLS)}  ({', '.join(POOLS)})")
    print(f"  tasks:             {args.tasks}")
    print(f"  task simulated ms: {args.task_ms:.1f}")
    print()

    # 1. Setup — registry + dispatcher + in-memory task store.
    #    Tight poll cadence so an empty queue resolves in tens of ms,
    #    not the dispatcher's 30s default.
    registry = InMemoryFleetRegistry()
    task_store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(
        store=task_store, poll_interval=0.01, poll_timeout=0.5,
    )

    # 2. Register the worker fleet.
    workers = _build_workers(per_tenant)
    worker_records: dict[str, tuple] = {}
    for w in workers:
        record = await registry.register_worker(
            name=w["name"],
            org_id=ORG_ID,
            capabilities=WorkerCapabilities(
                models_supported=w["models"],
                pool=w["pool"],
                labels={"project_id": w["project_id"]},
                max_concurrent=4,
            ),
        )
        await registry.approve_worker(record.id, approved_by="admin")
        worker_records[w["name"]] = (record, w)

    _line(" Workers registered ")
    print()
    by_tenant: dict[str, int] = defaultdict(int)
    by_pool: dict[str, int] = defaultdict(int)
    for w in workers:
        by_tenant[w["project_id"]] += 1
        by_pool[w["pool"]] += 1
    for tenant in TENANTS:
        n = by_tenant[tenant["project_id"]]
        print(f"  {tenant['id']:<10} {n:>3} workers  {_bar(n, max(by_tenant.values()))}")
    print()
    for pool in POOLS:
        n = by_pool[pool]
        print(f"  pool={pool:<8} {n:>3} workers  {_bar(n, max(by_pool.values()))}")
    print()

    # 3. Enqueue the workload.
    tasks = _build_tasks(args.tasks)
    for t in tasks:
        task_store.enqueue({
            "run_id": t["run_id"],
            "model": t["model"],
            "pool": t["pool"],
            "labels": {"project_id": t["project_id"]},
            "payload": f"work for {t['project_id']}",
            # Sealed-pillar requirement: every dispatched task declares
            # the sandbox it needs. This example is a load demo against
            # the general-purpose image with no outbound network — the
            # focus is dispatcher throughput, not third-party calls.
            "requires_sandbox_mode": SandboxMode.PER_RUN,
            "requires_image": (
                f"ghcr.io/sagewai/sandbox-general:{image_manifest.SDK_VERSION}"
            ),
            "requires_network_policy": NetworkPolicy.NONE,
        })

    _line(" Tasks enqueued ")
    print()
    by_tenant_t: dict[str, int] = defaultdict(int)
    for t in tasks:
        by_tenant_t[t["project_id"]] += 1
    for tenant in TENANTS:
        n = by_tenant_t[tenant["project_id"]]
        print(f"  {tenant['id']:<10} {n:>3} tasks    {_bar(n, max(by_tenant_t.values()))}")
    print()

    # 4. Drain concurrently — each worker runs in its own asyncio task.
    stats: dict[str, WorkerStats] = {
        w["name"]: WorkerStats(name=w["name"], project_id=w["project_id"])
        for w in workers
    }
    _line(" Dispatching… ")
    print()
    print(f"  Running {len(workers)} concurrent worker loops against the queue.")
    print()

    t_start = time.perf_counter()
    await asyncio.gather(*[
        _worker_loop(
            dispatcher=dispatcher,
            worker_record=worker_records[w["name"]][0],
            worker_meta=w,
            task_simulated_ms=args.task_ms,
            stats=stats[w["name"]],
        )
        for w in workers
    ])
    t_end = time.perf_counter()

    # 5. Throughput + latency stats.
    #
    # We report two times:
    # - **drain elapsed**: t_start → moment the last task was claimed.
    #   This is the real "queue went from full to empty" time and the
    #   honest number for the throughput claim.
    # - **wall elapsed**: t_start → all workers returned. Includes the
    #   dispatcher's empty-queue poll timeout (every worker waits one
    #   ``poll_timeout`` after the queue empties before returning ``None``).
    all_latencies: list[float] = [
        ms for s in stats.values() for ms in s.claim_latencies_ms
    ]
    total_claimed = sum(len(s.claimed) for s in stats.values())
    last_claim_t = max(
        (s.last_claim_t for s in stats.values() if s.last_claim_t > 0),
        default=t_end,
    )
    drain_elapsed = max(last_claim_t - t_start, 1e-9)
    wall_elapsed = t_end - t_start
    throughput = total_claimed / drain_elapsed

    _line(" Throughput + latency ")
    print()
    print(f"  total tasks claimed:   {total_claimed} / {args.tasks}")
    print(f"  drain elapsed:         {drain_elapsed * 1000.0:.1f}ms  "
          f"(t_start → last claim)")
    print(f"  wall elapsed:          {wall_elapsed * 1000.0:.1f}ms  "
          f"(includes {dispatcher._poll_timeout * 1000.0:.0f}ms "
          f"empty-queue poll timeout)")
    print(f"  throughput:            {throughput:.1f} tasks/sec  "
          f"(claimed / drain)")
    print()
    if all_latencies:
        p50 = _percentile(all_latencies, 50)
        p95 = _percentile(all_latencies, 95)
        p99 = _percentile(all_latencies, 99)
        mean = statistics.fmean(all_latencies)
        print(f"  claim latency mean:    {mean:.3f}ms")
        print(f"  claim latency p50:     {p50:.3f}ms")
        print(f"  claim latency p95:     {p95:.3f}ms")
        print(f"  claim latency p99:     {p99:.3f}ms")
    print()

    # 6. Per-tenant distribution.
    _line(" Per-tenant claim distribution ")
    print()
    claim_by_tenant: dict[str, int] = defaultdict(int)
    for s in stats.values():
        claim_by_tenant[s.project_id] += len(s.claimed)
    max_share = max(claim_by_tenant.values()) if claim_by_tenant else 0
    for tenant in TENANTS:
        proj = tenant["project_id"]
        enq = by_tenant_t[proj]
        clm = claim_by_tenant[proj]
        ok = "OK" if clm == enq else "!! "
        print(f"  {ok} {tenant['id']:<10} enqueued={enq:>3}  claimed={clm:>3}  {_bar(clm, max_share)}")
    print()

    # 7. Cross-tenant isolation invariant.
    #    A tenant-scoped task (carries a project_id label) must never be
    #    claimed by a worker of a different tenant. This is the Fleet
    #    multi-tenant guarantee — the load test would be useless without
    #    re-asserting it.
    _line(" Cross-tenant isolation invariant ")
    print()
    task_project: dict[str, str] = {t["run_id"]: t["project_id"] for t in tasks}
    leaks: list[tuple[str, str, str, str]] = []
    for s in stats.values():
        for run_id in s.claimed:
            tp = task_project[run_id]
            if tp != s.project_id:
                leaks.append((s.name, s.project_id, run_id, tp))
    if not leaks:
        print(f"  Zero cross-tenant leaks across {total_claimed} claimed tasks.")
    else:
        print(f"  {len(leaks)} LEAK(S) detected:")
        for worker_name, wp, run_id, tp in leaks[:10]:
            print(f"    {worker_name} (project={wp}) claimed {run_id} (project={tp})")
        if len(leaks) > 10:
            print(f"    … and {len(leaks) - 10} more")
    print()

    # 8. The proof — one-line summary line for copy-paste into a report.
    _line(" The proof ")
    print()
    proof = {
        "workers": actual_workers,
        "tenants": len(TENANTS),
        "tasks_enqueued": args.tasks,
        "tasks_claimed": total_claimed,
        "drain_elapsed_ms": round(drain_elapsed * 1000.0, 3),
        "wall_elapsed_ms": round(wall_elapsed * 1000.0, 1),
        "throughput_per_sec": round(throughput, 1),
        "claim_latency_p50_ms": round(_percentile(all_latencies, 50), 3),
        "claim_latency_p95_ms": round(_percentile(all_latencies, 95), 3),
        "claim_latency_p99_ms": round(_percentile(all_latencies, 99), 3),
        "cross_tenant_leaks": len(leaks),
    }
    print(json.dumps(proof, indent=2))
    print()

    # 9. HUD-recording pointer. The in-memory dispatcher used here keeps
    #    the example dependency-free; for the marketing-grade HUD demo
    #    (atelier#7 gap #3), the same dispatch shape is wired against a
    #    Postgres TaskStore so the admin UI observes live state.
    _line(" Iron Man HUD recording ")
    print()
    print("  This example feeds throughput numbers. To record the HUD with")
    print("  the same load shape:")
    print()
    print("    1. docker compose up -d postgres   (or point at an existing DB)")
    print("    2. just admin-dev                  (boots the admin UI + HUD)")
    print("    3. rerun this script against a Postgres TaskStore — see the")
    print("       atelier runbook for the swap (it's a 5-line change to use")
    print("       PostgresStore in place of InMemoryTaskStore).")
    print("    4. Open http://localhost:3000/hud-ironman and screen-record.")
    print()
    if leaks:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
