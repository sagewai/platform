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
"""Example 35 — Autopilot end-to-end with the hosted blueprint service.

**Freemium boundary:** this example requires the hosted ``sagewai-llm``
service (default: ``api.sagewai.ai``) or a local copy of the
``sagewai/sagewai-llm`` repo running on ``127.0.0.1:8100``. The other 32
examples in this directory run with no hosted service — pure OSS path.

The fullest autopilot demo: state a goal in plain English, the hosted
service generates a blueprint for it, the OSS framework parses + runs
it. This is the loop your operators see when they hit the autopilot
wizard in the admin UI.

The hosted service (``sagewai-llm``, private repo) is normally at
``api.sagewai.ai``. For local testing, run a copy yourself::

    git clone git@github.com:sagewai/sagewai-llm.git
    cd sagewai-llm
    uv run uvicorn 'sagewai_llm.app:create_app' --factory \\
        --host 127.0.0.1 --port 8100

Then run this example with::

    SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python 35_autopilot_hosted_service.py

Optional env vars:

- ``SAGEWAI_LLM_BASE_URL`` — required; URL of the hosted service.
- ``SAGEWAI_LIVE_SYNTHESIS=1`` — after the deterministic round-trips,
  also send an off-corpus goal to exercise the synthesis pathway and
  print the side-by-side path comparison.

What the example exercises:

1. ``InstanceIdentity`` — auto-generated HMAC identity for this client
2. ``BlueprintCache`` — TTL-bounded local cache (so retries are cheap)
3. ``SagewaiLLMClient.generate_blueprint(goal=...)`` — the round-trip
4. ``Blueprint.model_validate_json()`` — schema validation on the
   server's response
5. ``MissionDriver`` — runs each generated blueprint as a mission and
   prints real ``MissionRunResult`` numbers (status, steps, wall ms,
   cost USD). Every blueprint runs — there is no stub-skip branch.

This is the autopilot story end-to-end: HTTP, auth, cache,
schema, execution.

Requirements::

    pip install 'sagewai[autopilot]'
    # And a running sagewai-llm server (see above)

Usage::

    SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python 35_autopilot_hosted_service.py

    # To also exercise the synthesis pathway:
    SAGEWAI_LIVE_SYNTHESIS=1 SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python 35_autopilot_hosted_service.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.controller.executor import ExecutorConfig
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.errors import (
    ClientUnreachable,
    QuotaExceeded,
    ServiceError,
)
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity


# Three goals matching Plan C mock-LLM fixtures so the example runs
# end-to-end against the local dev stack without real API keys.
GOALS = [
    "track competitor pricing daily",
    "triage incoming support tickets",
    "extract data from PDFs",
]


def _default_base_url() -> str:
    return os.environ.get("SAGEWAI_LLM_BASE_URL", "http://127.0.0.1:8100")


async def _run_one(
    client: SagewaiLLMClient, goal: str, *, index: int, total: int,
) -> tuple[Blueprint | None, float, str]:
    """Round-trip one goal. Returns (blueprint, elapsed_ms, status_msg)."""
    print(f"  [{index}/{total}] generating blueprint for:")
    print(f"      \"{goal}\"")
    started = time.perf_counter()
    try:
        response = await client.generate_blueprint(goal=goal)
    except ClientUnreachable as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return None, elapsed_ms, f"server unreachable: {exc}"
    except QuotaExceeded as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return None, elapsed_ms, f"quota exceeded: {exc}"
    except ServiceError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return None, elapsed_ms, f"service error {exc.status_code}: {exc.body[:80]}"
    elapsed_ms = (time.perf_counter() - started) * 1000

    try:
        blueprint = Blueprint.model_validate_json(response.blueprint_json)
    except Exception as exc:
        return None, elapsed_ms, f"schema validation failed: {exc}"

    return blueprint, elapsed_ms, f"confidence={response.confidence:.2f}"


async def main() -> None:
    base_url = _default_base_url()
    print("─" * 72)
    print(" Sagewai Autopilot — hosted blueprint service end-to-end (example 35)")
    print("─" * 72)
    print(f"  base_url = {base_url}")
    print()

    # 1. Create an identity (in production this is persisted; here we
    #    use a fresh one each run).
    identity = InstanceIdentity.generate()
    print(f"  identity.instance_id = {identity.instance_id[:8]}…")
    print()

    # 2. Cache lives in a temp directory for the duration of the demo.
    with tempfile.TemporaryDirectory(prefix="sagewai-bp-cache-") as tmp:
        cache = BlueprintCache(Path(tmp), ttl_seconds=300)

        async with SagewaiLLMClient(
            base_url=base_url,
            identity=identity,
            cache=cache,
        ) as client:

            # 3. Round-trip each goal. Track latency for the perf table.
            print("─" * 72)
            print(" Round-trips")
            print("─" * 72)
            print()
            results: list[tuple[str, Blueprint | None, float, str]] = []
            for i, goal in enumerate(GOALS, start=1):
                bp, ms, status = await _run_one(
                    client, goal, index=i, total=len(GOALS),
                )
                results.append((goal, bp, ms, status))
                if bp is not None:
                    print(
                        f"      → blueprint {bp.id!r} v{bp.version} "
                        f"({len(bp.agent_graph.nodes)} node(s)), "
                        f"{ms:>6.1f}ms — {status}"
                    )
                else:
                    print(f"      → FAILED in {ms:>6.1f}ms — {status}")
                print()

            # 4. Performance table — what your operators see in the
            #    Observatory's autopilot latency panel.
            print("─" * 72)
            print(" Latency summary")
            print("─" * 72)
            print()
            ok = [r for r in results if r[1] is not None]
            failed = [r for r in results if r[1] is None]
            print(f"  successful generations:  {len(ok)}/{len(results)}")
            if ok:
                latencies = sorted(r[2] for r in ok)
                p50 = latencies[len(latencies) // 2]
                p99 = latencies[-1]
                avg = sum(latencies) / len(latencies)
                print(f"  p50 latency:             {p50:>6.1f}ms")
                print(f"  p99 latency:             {p99:>6.1f}ms")
                print(f"  avg latency:             {avg:>6.1f}ms")
            if failed:
                print(f"  failures:                {len(failed)}")
                for goal, _, _, status in failed:
                    print(f"    - \"{goal[:60]}…\": {status}")
            print()

            # 5. Quota status — what tier are we on?
            quota = client.last_quota
            if quota is not None:
                print(f"  quota tier:              {quota.tier}")
                print(f"  quota limit:             {quota.limit}")
                print(f"  quota endpoint:          {quota.endpoint}")
                print()

            # 6. Run each generated blueprint as a mission; print real numbers.
            print("─" * 72)
            print(" Mission runs — real MissionRunResult per goal")
            print("─" * 72)
            print()

            mission_results: list[tuple[str, str, float]] = []
            for goal, bp, gen_ms, status in results:
                if bp is None:
                    continue
                mission = Mission(
                    mission_id=f"ms-35-{bp.id[:8]}",
                    project_id="example-35",
                    blueprint_id=bp.id,
                    blueprint_version=bp.version,
                    slots={"__blueprint_json__": bp.model_dump_json()},
                )
                mission.transition_to(MissionState.APPROVED)
                mission.transition_to(MissionState.SCHEDULED)
                cfg = ExecutorConfig(
                    model=(
                        "gpt-4o-mini"
                        if os.environ.get("OPENAI_API_KEY")
                        else "claude-haiku-4-5-20251001"
                    ),
                    max_tool_iterations=3,
                )
                driver = MissionDriver(executor_config=cfg)
                print(f'  goal: "{goal}"')
                print(f"  blueprint: {bp.id} v{bp.version}")
                started = time.perf_counter()
                run = await driver.execute(mission)
                elapsed_ms = (time.perf_counter() - started) * 1000
                print(
                    f"  Mission status: {run.status} "
                    f"steps={len(run.steps)} "
                    f"mission_duration={run.duration_seconds:.3f}s "
                    f"wall={elapsed_ms:>6.1f}ms"
                )
                cost = sum(
                    (s.telemetry.cost_usd if s.telemetry else 0.0) for s in run.steps
                )
                print(f"  cost_usd: ${cost:.6f}")
                print()
                mission_results.append((goal, run.status, elapsed_ms))

            # 7. Honest performance summary
            print("─" * 72)
            print(" Performance summary")
            print("─" * 72)
            print()
            print(f"  {'goal':<40} {'status':<10} {'wall_ms':>10}")
            print(f"  {'-'*40} {'-'*10} {'-'*10}")
            for goal, status_str, ms in mission_results:
                print(f"  {goal[:40]:<40} {status_str:<10} {ms:>10.1f}")
            print()

            # 8. Live-synthesis opt-in — demonstrates both pathways in one run.
            if os.environ.get("SAGEWAI_LIVE_SYNTHESIS") == "1":
                off_corpus_goal = (
                    "design a custom CRM workflow for a 50-person legal firm"
                )
                print()
                print("─" * 72)
                print(" Synthesis path (off-corpus goal)")
                print("─" * 72)
                print()
                print(f"  goal: {off_corpus_goal!r}")
                print()
                async with SagewaiLLMClient(
                    base_url=base_url,
                    identity=identity,
                    cache=cache,
                ) as synth_client:
                    try:
                        synth_resp = await synth_client.generate_blueprint(
                            goal=off_corpus_goal,
                        )
                        bp = Blueprint.model_validate_json(synth_resp.blueprint_json)
                        print(f"  blueprint.id:    {bp.id}")
                        tier = getattr(synth_resp, "quality_tier", None) or "—"
                        lat = getattr(synth_resp, "latency_ms", None)
                        print(f"  quality_tier:    {tier}")
                        print(f"  confidence:      {synth_resp.confidence:.3f}")
                        if lat is not None:
                            print(f"  latency:         {lat:.1f}ms")
                        print()
                        print(
                            "  ─── deterministic path ───"
                            "  (goals above, retrieved from corpus)"
                        )
                        print(
                            "  ─── synthesis path       ───"
                            "  (goal above, generated on the fly)"
                        )
                    except (ClientUnreachable, ServiceError, QuotaExceeded) as exc:
                        print(f"  synthesis unavailable: {exc}")
                print()


if __name__ == "__main__":
    asyncio.run(main())
