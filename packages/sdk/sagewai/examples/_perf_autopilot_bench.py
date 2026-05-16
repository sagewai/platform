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
"""Autopilot perf benchmark — measures the SagewaiLLMClient round-trip.

Not an example you run as a demo. This is a developer tool that probes
the hosted blueprint service across concurrency levels and produces
the latency numbers we publish in the v1.0 launch claim.

Run a local server first (see Example 35 for the boot command), then::

    SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python packages/sdk/sagewai/examples/_perf_autopilot_bench.py

Output: a table of (concurrency, p50, p95, p99, throughput) and a JSON
snapshot. Saved nowhere — pipe into ``tee`` if you want to keep it.

Defaults: 100 requests across concurrency 1, 4, 16. Tunable via env vars
``BENCH_TOTAL`` and ``BENCH_CONCURRENCY``.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity


GOAL_TEMPLATES = [
    "Page on-call when API error rate exceeds {n}%",
    "Triage support emails into urgency tier {n}",
    "Run nightly invoice reconciliation for region-{n}",
    "Generate weekly ARR report for cohort-{n}",
    "Detect churn risk in customer segment {n}",
]


def _goals(total: int) -> list[str]:
    out: list[str] = []
    for i in range(total):
        template = GOAL_TEMPLATES[i % len(GOAL_TEMPLATES)]
        out.append(template.format(n=(i % 10) + 1))
    return out


async def _one_request(client: SagewaiLLMClient, goal: str) -> tuple[bool, float]:
    started = time.perf_counter()
    try:
        await client.generate_blueprint(goal=goal)
        return True, (time.perf_counter() - started) * 1000
    except Exception:
        return False, (time.perf_counter() - started) * 1000


async def _run_at_concurrency(
    client: SagewaiLLMClient, goals: list[str], concurrency: int,
) -> dict[str, float]:
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(goal: str) -> tuple[bool, float]:
        async with sem:
            return await _one_request(client, goal)

    started = time.perf_counter()
    results = await asyncio.gather(*[_bounded(g) for g in goals])
    wall_seconds = time.perf_counter() - started

    successes = [ms for ok, ms in results if ok]
    failures = sum(1 for ok, _ in results if not ok)

    if not successes:
        return {
            "concurrency": concurrency,
            "total": len(goals),
            "ok": 0,
            "fail": failures,
            "wall_s": round(wall_seconds, 2),
            "throughput_rps": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
        }

    successes_sorted = sorted(successes)
    return {
        "concurrency": concurrency,
        "total": len(goals),
        "ok": len(successes),
        "fail": failures,
        "wall_s": round(wall_seconds, 2),
        "throughput_rps": round(len(successes) / wall_seconds, 1),
        "p50_ms": round(statistics.median(successes_sorted), 1),
        "p95_ms": round(successes_sorted[int(len(successes_sorted) * 0.95) - 1], 1),
        "p99_ms": round(successes_sorted[int(len(successes_sorted) * 0.99) - 1], 1),
    }


async def main() -> None:
    base_url = os.environ.get("SAGEWAI_LLM_BASE_URL", "http://127.0.0.1:8100")
    total = int(os.environ.get("BENCH_TOTAL", "100"))
    concurrency_levels = [
        int(c) for c in os.environ.get("BENCH_CONCURRENCY", "1,4,16").split(",")
    ]

    print("─" * 72)
    print(" Autopilot perf bench")
    print("─" * 72)
    print(f"  base_url    = {base_url}")
    print(f"  requests    = {total}")
    print(f"  concurrency = {concurrency_levels}")
    print()

    identity = InstanceIdentity.generate()
    with tempfile.TemporaryDirectory(prefix="sagewai-bench-") as tmp:
        cache = BlueprintCache(Path(tmp), ttl_seconds=300)
        async with SagewaiLLMClient(
            base_url=base_url, identity=identity, cache=cache,
        ) as client:
            print(
                f"  {'conc':>4} {'total':>5} {'ok':>4} {'fail':>4} "
                f"{'wall':>6} {'rps':>7} {'p50':>7} {'p95':>7} {'p99':>7}"
            )
            print(
                f"  {'-'*4} {'-'*5} {'-'*4} {'-'*4} "
                f"{'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*7}"
            )
            results: list[dict] = []
            for c in concurrency_levels:
                goals = _goals(total)
                row = await _run_at_concurrency(client, goals, c)
                results.append(row)
                print(
                    f"  {row['concurrency']:>4} {row['total']:>5} "
                    f"{row['ok']:>4} {row['fail']:>4} "
                    f"{row['wall_s']:>6} {row['throughput_rps']:>7} "
                    f"{row['p50_ms']:>7} {row['p95_ms']:>7} {row['p99_ms']:>7}"
                )
            print()
            print(json.dumps({"runs": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
