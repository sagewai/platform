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
"""Example 28 — Autopilot quickstart.

**Freemium boundary:** when ``SAGEWAI_LLM_BASE_URL`` is set the example
exercises a live :class:`GoalRouter` against the hosted (or local)
``sagewai-llm`` service. When unset, the offline path stubs the
network with a dead transport and demonstrates the routing-result
handling surface without any service.

What's exercised:

- :class:`GoalRouter` against a real server (online) or dead transport (offline)
- All three :class:`RoutingResult` variants — :class:`AutoRouted`,
  :class:`PickerNeeded`, :class:`SynthesisNeeded`
- :class:`Mission` lifecycle — DRAFT → APPROVED → SCHEDULED
- :class:`MissionDriver` — runs a synthetic blueprint end-to-end

Both paths complete in under 10 seconds on a clean machine.

Requirements::

    pip install sagewai
    # Optional: a running sagewai-llm at SAGEWAI_LLM_BASE_URL
    #   docker compose up -d  (in the sagewai-llm repo)
    #   export SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100

Usage::

    # Offline path — no env vars needed
    python packages/sdk/sagewai/examples/28_autopilot_quickstart.py

    # Online path — requires sagewai-llm running locally
    SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 \\
        python packages/sdk/sagewai/examples/28_autopilot_quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


# ── module-level constants ─────────────────────────────────────────


# Three demo goals chosen so a Plan-C-seeded server returns one of
# each RoutingResult variant. The "expected" label is informational —
# we print it next to the actual decision so the reader can verify.
_DEMO_GOALS: list[tuple[str, str]] = [
    ("triage the incident: high CPU on prod-web-01", "auto_routed"),
    ("watch our top three competitors' pricing pages weekly", "picker_needed"),
    ("plan my anniversary dinner reservation", "synthesis_needed"),
]


# ── helpers ────────────────────────────────────────────────────────


def _make_dead_client(cache_dir: Path):
    """Build a SagewaiLLMClient that always raises ClientUnreachable."""
    import httpx

    from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
    from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
    from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity

    class _DeadTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("mock: no network")

    dead_http = httpx.AsyncClient(transport=_DeadTransport())
    identity = InstanceIdentity.generate()
    cache = BlueprintCache(cache_dir / "bp_cache", ttl_seconds=3600)
    return SagewaiLLMClient(
        base_url="http://localhost:9999",
        identity=identity,
        cache=cache,
        http_client=dead_http,
    )


def _make_live_client(cache_dir: Path, base_url: str):
    """Build a SagewaiLLMClient pointed at a real server."""
    from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
    from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
    from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity

    identity = InstanceIdentity.generate()
    cache = BlueprintCache(cache_dir / "bp_cache", ttl_seconds=300)
    return SagewaiLLMClient(
        base_url=base_url,
        identity=identity,
        cache=cache,
    )


def _make_synthetic_mission():
    """Return a SCHEDULED mission backed by the synthetic scheduled blueprint."""
    from sagewai.autopilot._types import MissionState
    from sagewai.autopilot.mission import Mission

    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint  # type: ignore[import]

    bp = make_synthetic_scheduled_blueprint()
    slots: dict = {
        "vendors": ["https://example.com"],
        "schedule": "0 9 * * 1-5",
        "__blueprint_json__": bp.model_dump_json(),
    }
    mission = Mission(
        mission_id="ms-quickstart-001",
        project_id="quickstart",
        blueprint_id=bp.id,
        blueprint_version=bp.version,
        slots=slots,
    )
    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)
    return mission


def _print_routing_result(goal: str, expected: str, result) -> None:
    from sagewai.autopilot import AutoRouted, PickerNeeded, SynthesisNeeded
    from sagewai.autopilot.blueprint import Blueprint

    print(f'  goal:     "{goal}"')
    print(f"  expected: {expected}")
    print(f"  routing result: {result.kind}")
    if isinstance(result, AutoRouted):
        bp = Blueprint.model_validate_json(result.ranked.blueprint_json)
        tier = getattr(result.ranked, "quality_tier", None) or "—"
        print(f"    → auto-routed to blueprint id={bp.id!r}")
        print(f"    → score={result.ranked.score:.3f}  tier={tier}")
    elif isinstance(result, PickerNeeded):
        print(f"    → {len(result.candidates)} candidates need operator pick:")
        for i, c in enumerate(result.candidates[:3]):
            tier = getattr(c, "quality_tier", None) or "—"
            print(f"        [{i}] score={c.score:.3f}  tier={tier}")
    elif isinstance(result, SynthesisNeeded):
        print("    → no near match — synthesis path would generate one")
    print()


# ── main ───────────────────────────────────────────────────────────


async def main() -> None:
    from sagewai.autopilot import (
        ConfidenceConfig,
        GoalRouter,
        MissionDriver,
    )

    base_url = os.environ.get("SAGEWAI_LLM_BASE_URL")

    print("─" * 72)
    print(" Sagewai Autopilot — quickstart (example 28)")
    print("─" * 72)
    print()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if base_url:
            print(f"  Live path: SAGEWAI_LLM_BASE_URL={base_url}")
            print()
            client = _make_live_client(tmp_path, base_url)
            async with client:
                router = GoalRouter(client=client, config=ConfidenceConfig())
                print("─" * 72)
                print(" Routing three demo goals against the live server")
                print("─" * 72)
                print()
                for goal, expected in _DEMO_GOALS:
                    result = await router.route(goal)
                    _print_routing_result(goal, expected, result)
        else:
            print("  Offline path: SAGEWAI_LLM_BASE_URL not set")
            print("  (using a dead transport that raises ClientUnreachable —")
            print("   GoalRouter falls back to SynthesisNeeded.)")
            print()
            client = _make_dead_client(tmp_path)
            async with client:
                router = GoalRouter(client=client, config=ConfidenceConfig())
                print("─" * 72)
                print(" Routing one demo goal against the dead transport")
                print("─" * 72)
                print()
                goal = _DEMO_GOALS[0][0]
                result = await router.route(goal)
                _print_routing_result(goal, "synthesis_needed (offline)", result)

        # ── AutoRouted-path demo via synthetic fixture ─────────────
        print("─" * 72)
        print(" AutoRouted path — synthetic blueprint mission run")
        print("─" * 72)
        print()
        try:
            mission = _make_synthetic_mission()
            print(f"  Mission {mission.mission_id} created (DRAFT → SCHEDULED)")
            driver = MissionDriver()
            print("  Running mission ...")
            run_result = await driver.execute(mission)
            print(
                f"  status={run_result.status} "
                f"steps={len(run_result.steps)} "
                f"duration={run_result.duration_seconds:.3f}s"
            )
        except ImportError:
            print(
                "  Synthetic fixtures not available (running outside source tree) — "
                "skipping AutoRouted demo."
            )
        print()

    # ── proof ──────────────────────────────────────────────────────
    print("─" * 72)
    print(" The proof")
    print("─" * 72)
    print()
    if base_url:
        print("  You saw three routing decisions made against a real server:")
        print("  one auto-routed match, one operator-pick fan-out, one synthesis")
        print("  fallback. Plus a synthetic mission ran end-to-end locally.")
    else:
        print("  You saw the routing-result handling surface plus a synthetic")
        print("  mission running end-to-end. To exercise live retrieval, point")
        print("  SAGEWAI_LLM_BASE_URL at a running sagewai-llm server.")
    print()
    print("  Done.")


if __name__ == "__main__":
    asyncio.run(main())
