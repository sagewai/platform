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
"""Example 43 — Observatory live: Tuesday morning at a 200-person SaaS.

Drives the admin REST API with a realistic mixed-tenant workload so
the Observatory dashboards (Grafana board + Iron Man HUD) light up
with real numbers — not synthetic, not pre-canned. Three projects
run concurrently for a few minutes:

- acme-support       — high-volume ticket triage (4 workers, cheap model)
- globex-codereview  — low-volume PR review     (2 workers, expensive model)
- initech-sales      — medium-volume enrichment (2 workers, mixed)

Every HTTP call hits the admin's instrumented FastAPI routes, so the
OTel pipeline ships metrics to VictoriaMetrics, Grafana panels move,
HUD KPIs climb, and the project bar actually has projects to switch
between.

What's exercised:
- POST /api/v1/setup, /api/v1/auth/login (one-shot bootstrap)
- POST /api/v1/projects (per tenant)
- POST /playground/agent (per agent — emits agent.created OTel event)
- POST /api/v1/fleet/register, /heartbeat, /claim, /report
  (worker lifecycle through the admin's real fleet dispatcher)
- A few intentional 401/404 probes so the Status Code panel has
  a non-trivial distribution
- GET fan-out (/playground/agents, /api/v1/projects, /api/v1/fleet/workers)
  so the Request Rate by Route panel has more than one line

Requirements::

    pip install sagewai httpx
    docker compose -f docker-compose.observability.yml up -d   # observability
    sagewai admin serve --host 127.0.0.1 --port 8000           # admin backend
    # (optional, for the HUD)
    just admin-dev    # admin frontend on :3008

Usage::

    python 43_observatory_live.py
    python 43_observatory_live.py --duration 300   # default 180s
    python 43_observatory_live.py --backend http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


# ── module constants ──────────────────────────────────────────────


@dataclass(frozen=True)
class Tenant:
    slug: str
    name: str
    description: str
    workers: int
    pool: str
    model: str
    task_interval_s: tuple[float, float]   # (min, max) seconds between tasks
    tools: tuple[str, ...]


TENANTS: tuple[Tenant, ...] = (
    Tenant(
        slug="acme-support",
        name="Acme Support",
        description="Customer-support ticket triage and response drafting.",
        workers=4,
        pool="default",
        model="claude-haiku-4-5",
        task_interval_s=(1.0, 2.5),
        tools=("inbox.read", "kb.search", "ticket.update"),
    ),
    Tenant(
        slug="globex-codereview",
        name="Globex Code Review",
        description="Pull-request review and security-scan summary.",
        workers=2,
        pool="reviewers",
        model="claude-sonnet-4-6",
        task_interval_s=(5.0, 10.0),
        tools=("github.pr.read", "lint.run", "review.comment"),
    ),
    Tenant(
        slug="initech-sales",
        name="Initech Sales",
        description="Inbound-lead enrichment and qualification.",
        workers=2,
        pool="default",
        model="claude-haiku-4-5",
        task_interval_s=(2.0, 4.0),
        tools=("crm.lookup", "web.search", "enrich.call"),
    ),
)


AGENTS_PER_TENANT: tuple[tuple[str, str, str], ...] = (
    # (suffix, role, system_prompt fragment)
    ("triager",      "triager",   "Read incoming items and assign them to the right queue."),
    ("drafter",      "drafter",   "Draft a first-pass response for the assigned item."),
    ("escalator",    "escalator", "Decide whether the item needs human escalation."),
    ("summariser",   "summariser","Summarise the day's activity for the manager."),
)


# ── helpers ─────────────────────────────────────────────────────


@dataclass
class _Stats:
    started_at: float = field(default_factory=time.monotonic)
    http_calls: int = 0
    http_4xx: int = 0
    http_5xx: int = 0
    tasks_enqueued: int = 0
    tasks_completed: int = 0
    workers_registered: int = 0
    agents_created: int = 0
    projects_created: int = 0
    by_tenant: dict[str, int] = field(default_factory=dict)


def _format_int(n: int) -> str:
    return f"{n:>6,}"


def _bar(value: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "·" * width
    filled = round(width * value / total)
    return "█" * filled + "·" * (width - filled)


async def _client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(10.0, connect=2.0),
        cookies={},
    )


async def _bootstrap(http: httpx.AsyncClient, stats: _Stats) -> None:
    """Run the setup wizard if needed, then login."""
    r = await http.get("/api/v1/setup/status")
    stats.http_calls += 1
    if r.status_code != 200:
        raise SystemExit(
            f"backend unreachable at {http.base_url} ({r.status_code}); "
            "is `sagewai admin serve` running?"
        )
    if r.json().get("setup_required"):
        r = await http.post("/api/v1/setup", json={
            "org_name": "Sagewai Demo HQ",
            "org_slug": "sagewai-demo",
            "contact_email": "ops@sagewai.demo",
            "timezone": "UTC",
            "app_name": "Observatory Live",
            "app_description": "Example 43 driver",
            "admin_name": "Ops Lead",
            "admin_email": "ops@sagewai.demo",
            "admin_password": "ObsLive!2026",
        })
        stats.http_calls += 1
        if r.status_code != 200:
            raise SystemExit(f"setup failed: {r.status_code} {r.text[:200]}")

    r = await http.post("/api/v1/auth/login", json={
        "email": "ops@sagewai.demo",
        "password": "ObsLive!2026",
    })
    stats.http_calls += 1
    if r.status_code != 200:
        raise SystemExit(f"login failed: {r.status_code} {r.text[:200]}")
    # Cookie auto-attached to subsequent requests via cookies jar


async def _create_projects(http: httpx.AsyncClient, stats: _Stats) -> None:
    for t in TENANTS:
        r = await http.post("/api/v1/projects", json={
            "name": t.name,
            "slug": t.slug,
            "environment": "production",
        })
        stats.http_calls += 1
        if r.status_code in (201, 409):  # 409 == already exists, fine
            stats.projects_created += 1 if r.status_code == 201 else 0
        else:
            print(f"  ! project {t.slug}: {r.status_code} {r.text[:120]}")


async def _create_agents(http: httpx.AsyncClient, stats: _Stats) -> None:
    for t in TENANTS:
        for suffix, role, prompt in AGENTS_PER_TENANT:
            name = f"{t.slug}-{suffix}"
            r = await http.post(
                "/playground/agent",
                json={
                    "name": name,
                    "model": t.model,
                    "system_prompt": prompt,
                    "strategy": "react",
                    "temperature": 0.2,
                    "tools": list(t.tools),
                    "metadata": {"role": role, "tenant": t.slug},
                },
                headers={"X-Project-ID": t.slug},
            )
            stats.http_calls += 1
            if r.status_code == 201:
                stats.agents_created += 1
            else:
                print(f"  ! agent {name}: {r.status_code} {r.text[:120]}")


async def _register_workers(
    http: httpx.AsyncClient, stats: _Stats,
) -> list[dict[str, Any]]:
    workers: list[dict[str, Any]] = []
    for t in TENANTS:
        for i in range(t.workers):
            name = f"{t.slug}-w{i:02d}"
            r = await http.post(
                "/api/v1/fleet/register",
                json={
                    "name": name,
                    "org_id": t.slug,
                    "models": [t.model],
                    "pool": t.pool,
                    "labels": {"tenant": t.slug, "role": "worker"},
                    "max_concurrent": 2,
                },
                headers={"X-Project-ID": t.slug},
            )
            stats.http_calls += 1
            if r.status_code == 201:
                payload = r.json()
                workers.append({
                    "id": payload["worker_id"],
                    "name": name,
                    "tenant": t,
                })
                stats.workers_registered += 1
            else:
                print(f"  ! register {name}: {r.status_code} {r.text[:120]}")
    return workers


_PROMPTS: tuple[str, ...] = (
    "Triage this morning's batch of customer tickets.",
    "Draft a response for the password-reset thread.",
    "Summarise yesterday's escalations for the ops standup.",
    "Review the incoming PR and flag risky changes.",
    "Enrich today's inbound leads with firmographic data.",
)


async def _run_invoker(
    http: httpx.AsyncClient, stats: _Stats, t: Tenant, deadline: float,
) -> None:
    """Fire /playground/run for this tenant's agents at a realistic pace.

    Each call exercises the full agent-run path on the admin backend
    (logs, OTel counters, run-record persistence). Without an API key
    configured for the tenant's model the run fails fast with
    `agent.run.error` — that's fine: the routes are exercised, the
    HUD's run counter ticks, and the Status Code panel sees the 5xx.
    """
    agents = [f"{t.slug}-{suffix}" for suffix, _, _ in AGENTS_PER_TENANT]
    seq = 0
    while time.monotonic() < deadline:
        seq += 1
        agent_name = random.choice(agents)
        prompt = random.choice(_PROMPTS)
        try:
            async with http.stream(
                "POST",
                "/playground/run",
                json={"agent_name": agent_name, "message": prompt},
                headers={"X-Project-ID": t.slug},
                timeout=httpx.Timeout(15.0),
            ) as resp:
                stats.http_calls += 1
                # Drain the SSE stream — we don't care about the body,
                # only that the route was exercised end-to-end.
                async for _ in resp.aiter_lines():
                    pass
                if 200 <= resp.status_code < 300:
                    stats.tasks_enqueued += 1
                    stats.by_tenant[t.slug] = stats.by_tenant.get(t.slug, 0) + 1
                elif 400 <= resp.status_code < 500:
                    stats.http_4xx += 1
                else:
                    stats.http_5xx += 1
        except httpx.HTTPError:
            # Stream errors during a failed run are expected when the
            # backend has no API key — count it as a 5xx for the panels.
            stats.http_5xx += 1
            stats.http_calls += 1
        await asyncio.sleep(random.uniform(*t.task_interval_s))


async def _worker_loop(
    http: httpx.AsyncClient,
    stats: _Stats,
    worker: dict[str, Any],
    deadline: float,
) -> None:
    """Run one worker's claim/heartbeat/report loop."""
    t: Tenant = worker["tenant"]
    last_heartbeat = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()

        # Heartbeat every ~10s
        if now - last_heartbeat > 10:
            r = await http.post(
                "/api/v1/fleet/heartbeat",
                json={"worker_id": worker["id"]},
                headers={"X-Project-ID": t.slug},
            )
            stats.http_calls += 1
            last_heartbeat = now

        # Try to claim a task
        r = await http.post(
            "/api/v1/fleet/claim",
            json={
                "worker_id": worker["id"],
                "org_id": t.slug,
                "models": [t.model],
                "pool": t.pool,
                "labels": {"project_id": t.slug},
            },
            headers={"X-Project-ID": t.slug},
        )
        stats.http_calls += 1

        if r.status_code == 200 and r.content:
            task = r.json()
            # Simulate work: short-but-variable per-task latency.
            await asyncio.sleep(random.uniform(0.4, 1.8))
            # Report completion (or, occasionally, failure for the
            # Status Code panel).
            status = "failed" if random.random() < 0.04 else "completed"
            r = await http.post(
                "/api/v1/fleet/report",
                json={
                    "worker_id": worker["id"],
                    "org_id": t.slug,
                    "run_id": task["run_id"],
                    "status": status,
                    "output": "ok" if status == "completed" else None,
                    "error": "transient upstream timeout" if status == "failed" else None,
                },
                headers={"X-Project-ID": t.slug},
            )
            stats.http_calls += 1
            if status == "completed":
                stats.tasks_completed += 1
        else:
            # No task ready — short backoff
            await asyncio.sleep(random.uniform(0.8, 1.5))


async def _list_pollers(
    http: httpx.AsyncClient, stats: _Stats, deadline: float,
) -> None:
    """Periodic GET fan-out so the Request Rate by Route panel has shape."""
    while time.monotonic() < deadline:
        for path in (
            "/api/v1/projects",
            "/playground/agents",
            "/api/v1/fleet/workers",
            "/api/v1/health/summary",
        ):
            r = await http.get(path)
            stats.http_calls += 1
            if r.status_code >= 500:
                stats.http_5xx += 1
        await asyncio.sleep(2.0)


async def _error_probes(
    http: httpx.AsyncClient, stats: _Stats, deadline: float,
) -> None:
    """Trigger occasional 4xx/404 so the Status Code panel has bands."""
    while time.monotonic() < deadline:
        # 401 — drop the auth cookie for one call
        async with httpx.AsyncClient(
            base_url=str(http.base_url), timeout=httpx.Timeout(5.0),
        ) as bare:
            r = await bare.get("/api/v1/auth/me")
            stats.http_calls += 1
            if 400 <= r.status_code < 500:
                stats.http_4xx += 1
        # 404 — non-existent project slug
        r = await http.get(f"/api/v1/projects/{random.choice(['ghost', 'void', 'noop'])}")
        stats.http_calls += 1
        if 400 <= r.status_code < 500:
            stats.http_4xx += 1
        await asyncio.sleep(7.0)


async def _print_progress(
    stats: _Stats, deadline: float, total: float,
) -> None:
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - stats.started_at
        pct = min(100, int(elapsed * 100 / total))
        print(
            f"\r  [{pct:>3}%] {_bar(int(pct), 100)}  "
            f"calls={_format_int(stats.http_calls)}  "
            f"enq={_format_int(stats.tasks_enqueued)}  "
            f"done={_format_int(stats.tasks_completed)}  "
            f"4xx={_format_int(stats.http_4xx)}",
            end="", flush=True,
        )
        await asyncio.sleep(2.0)
    print()


# ── main ──────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser(description="Observatory live load driver.")
    ap.add_argument(
        "--backend", default="http://127.0.0.1:8000",
        help="Admin backend base URL (default: %(default)s)",
    )
    ap.add_argument(
        "--duration", type=int, default=180,
        help="How long to drive load, in seconds (default: %(default)s)",
    )
    args = ap.parse_args()

    print("─" * 72)
    print(" Sagewai — Observatory live (example 43)")
    print("─" * 72)
    print()
    print(f"  Backend       : {args.backend}")
    print(f"  Run duration  : {args.duration}s")
    print(f"  Tenants       : {', '.join(t.slug for t in TENANTS)}")
    print(f"  Agents        : {len(TENANTS) * len(AGENTS_PER_TENANT)}")
    print(f"  Workers       : {sum(t.workers for t in TENANTS)}")
    print()

    stats = _Stats()
    async with await _client(args.backend) as http:
        print("─── Bootstrap ────────────────────────────────────────────")
        await _bootstrap(http, stats)
        await _create_projects(http, stats)
        await _create_agents(http, stats)
        workers = await _register_workers(http, stats)
        print(f"  ✓ projects={stats.projects_created}  "
              f"agents={stats.agents_created}  "
              f"workers={stats.workers_registered}")
        print()

        print("─── Driving load ───────────────────────────────────────")
        deadline = time.monotonic() + args.duration

        producers = [_run_invoker(http, stats, t, deadline) for t in TENANTS]
        worker_tasks = [_worker_loop(http, stats, w, deadline) for w in workers]
        ancillary = [
            _list_pollers(http, stats, deadline),
            _error_probes(http, stats, deadline),
            _print_progress(stats, deadline, args.duration),
        ]

        await asyncio.gather(
            *producers, *worker_tasks, *ancillary,
            return_exceptions=False,
        )

    elapsed = time.monotonic() - stats.started_at
    print()
    print("─── The proof ────────────────────────────────────────────")
    print(f"  Wall time           : {elapsed:.1f}s")
    print(f"  HTTP calls          : {_format_int(stats.http_calls)}  "
          f"({stats.http_calls / elapsed:.1f}/s)")
    print(f"  4xx responses       : {_format_int(stats.http_4xx)}")
    print(f"  5xx responses       : {_format_int(stats.http_5xx)}")
    print(f"  Tasks enqueued      : {_format_int(stats.tasks_enqueued)}")
    print(f"  Tasks completed     : {_format_int(stats.tasks_completed)}")
    print()
    print("  Per tenant:")
    enq_total = max(stats.tasks_enqueued, 1)
    for t in TENANTS:
        n = stats.by_tenant.get(t.slug, 0)
        print(f"    {t.slug:<22}  {_bar(n, enq_total)}  {_format_int(n)}")
    print()
    print("  Open the dashboards:")
    print("    Grafana board       http://localhost:3000")
    print("                        (anonymous read; admin/admin to edit)")
    print("    Iron Man HUD        http://localhost:3008/hud-ironman")
    print("                        (admin login: ops@sagewai.demo / ObsLive!2026)")
    print()
    print("  The data the dashboards just rendered is the data this run")
    print("  produced — every panel is sourced from real HTTP traffic and")
    print("  real fleet activity, not pre-canned metrics.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
