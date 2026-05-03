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

**Freemium boundary:** the autopilot path that ships an end-to-end mission
in production needs the hosted ``sagewai-llm`` service (default:
``api.sagewai.ai``) or a local copy of the ``sagewai/sagewai-llm`` repo
running on ``127.0.0.1:8100``. *This* example is the offline preview —
it stubs the client out so you can see the routing surfaces without a
service. The other 32 examples in this directory run with no hosted
service — pure OSS path.

Demonstrates the end-to-end autopilot flow without any external services:

1. Build a :class:`GoalRouter` backed by a mock :class:`SagewaiLLMClient`.
2. Route a plain-English goal — the mock always returns
   :class:`SynthesisNeeded` (no real blueprints in OSS), which triggers the
   synthesis path.
3. Show how to handle each :class:`RoutingResult` variant in code.
4. For an :class:`AutoRouted` result (demonstrated via direct construction):
   create a :class:`Mission`, approve it, schedule it, and drive it with
   :class:`MissionDriver`.
5. Print the :class:`MissionRunResult`.

**No network calls are made.** The ``SagewaiLLMClient`` is constructed with
a custom ``httpx.AsyncClient`` that raises ``ClientUnreachable`` immediately,
so :class:`GoalRouter` falls back to :class:`SynthesisNeeded`. The
:class:`AutoRouted` path is exercised by constructing the result directly
from the synthetic test blueprint that ships with the OSS repo.

Requirements::

    pip install sagewai

Usage::

    python packages/sdk/sagewai/examples/28_autopilot_quickstart.py

Typical output::

    [autopilot] goal: "run daily competitive research on 3 vendors"
    [autopilot] routing result: synthesis_needed
    [autopilot] No matching blueprint — synthesis path would generate one.
    [autopilot] Demonstrating AutoRouted path with synthetic blueprint ...
    [autopilot] Mission ms-quickstart-001 created (state=DRAFT)
    [autopilot] Mission approved + scheduled
    [autopilot] Running mission ...
    [autopilot] Result: status=completed steps=2 duration=...s
    [autopilot] Done.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(cache_dir: Path):
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


def _make_synthetic_mission():
    """Return a SCHEDULED mission backed by the synthetic scheduled blueprint."""
    from sagewai.autopilot._types import MissionState
    from sagewai.autopilot.mission import Mission

    # Import the synthetic fixture — these are OSS test fixtures only;
    # production blueprints live on the hosted service.
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint  # type: ignore[import]

    bp = make_synthetic_scheduled_blueprint()
    # Build minimal slots
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


# ---------------------------------------------------------------------------
# Main async entrypoint
# ---------------------------------------------------------------------------


async def _async_main() -> None:
    from sagewai.autopilot import (
        AutoRouted,
        ConfidenceConfig,
        GoalRouter,
        MissionDriver,
        PickerNeeded,
        SynthesisNeeded,
    )

    goal_text = "run daily competitive research on 3 vendors"
    print(f'[autopilot] goal: "{goal_text}"')

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client = _make_mock_client(tmp_path)

        async with client:
            router = GoalRouter(
                client=client,
                config=ConfidenceConfig(),
            )
            result = await router.route(goal_text)

        print(f"[autopilot] routing result: {result.kind}")

        # ── Handle each RoutingResult variant ──────────────────────
        if isinstance(result, AutoRouted):
            print(f"[autopilot] Auto-routed — preview:\n{result.preview}")

        elif isinstance(result, PickerNeeded):
            print(f"[autopilot] Picker needed — {len(result.candidates)} candidates:")
            for i, c in enumerate(result.candidates):
                print(f"  [{i}] score={c.score:.3f}")

        elif isinstance(result, SynthesisNeeded):
            print("[autopilot] No matching blueprint — synthesis path would generate one.")

        # ── Demonstrate the AutoRouted path with a synthetic fixture ─
        print("[autopilot] Demonstrating AutoRouted path with synthetic blueprint ...")

        try:
            mission = _make_synthetic_mission()
            print(f"[autopilot] Mission {mission.mission_id} created (state=DRAFT → SCHEDULED)")
            print("[autopilot] Mission approved + scheduled")

            driver = MissionDriver()
            print("[autopilot] Running mission ...")
            run_result = await driver.execute(mission)
            print(
                f"[autopilot] Result: status={run_result.status} "
                f"steps={len(run_result.steps)} "
                f"duration={run_result.duration_seconds:.3f}s"
            )

        except ImportError:
            # Running outside the SDK source tree — synthetic fixtures are
            # not available. This is fine in a standalone install.
            print(
                "[autopilot] Synthetic fixtures not available in installed package — "
                "skipping AutoRouted demo (requires running from source tree)."
            )

    print("[autopilot] Done.")


def main() -> None:
    """Run the autopilot quickstart example."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
