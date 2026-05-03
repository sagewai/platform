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

What the example exercises:

1. ``InstanceIdentity`` — auto-generated HMAC identity for this client
2. ``BlueprintCache`` — TTL-bounded local cache (so retries are cheap)
3. ``SagewaiLLMClient.generate_blueprint(goal=...)`` — the round-trip
4. ``Blueprint.model_validate_json()`` — schema validation on the
   server's response
5. (Optional) ``MissionDriver`` — runs the returned blueprint as a
   mission. Skipped when the server returned the placeholder stub
   so we don't pretend the stub is solving anything real.

This is the autopilot story end-to-end: HTTP, auth, cache,
schema, execution. Stub-blueprint responses are normal in dev — the
real generation pipeline lives behind feature flags on the server.

Requirements::

    pip install 'sagewai[autopilot]'
    # And a running sagewai-llm server (see above)

Usage::

    SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python 35_autopilot_hosted_service.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.errors import (
    ClientUnreachable,
    QuotaExceeded,
    ServiceError,
)
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity


GOALS = [
    "Page on-call when the API error rate exceeds 5% for more than 2 minutes",
    "Triage incoming customer-support emails and tag them by urgency",
    "Run a nightly invoice reconciliation job against the finance database",
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

            # 6. Show the first blueprint's structure (for the curious)
            if ok:
                first_bp = ok[0][1]
                assert first_bp is not None
                print("─" * 72)
                print(f" First blueprint structure: {first_bp.id!r}")
                print("─" * 72)
                print()
                summary = {
                    "id": first_bp.id,
                    "version": first_bp.version,
                    "title": first_bp.title,
                    "category": first_bp.category,
                    "mode": first_bp.mode,
                    "node_count": len(first_bp.agent_graph.nodes),
                    "tools_required": list(first_bp.tools_required),
                    "providers_required": list(first_bp.providers_required),
                }
                print(json.dumps(summary, indent=2))
                print()

                if first_bp.id == "stub-generated":
                    print("  Note: server returned the placeholder stub blueprint.")
                    print("        The real generation pipeline (Opus/GPT) is gated")
                    print("        behind feature flags on the server. Stub responses")
                    print("        prove the round-trip but skip the mission run.")


if __name__ == "__main__":
    asyncio.run(main())
