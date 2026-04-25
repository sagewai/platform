# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for FleetMissionAdapter and fleet-enabled MissionDriver.

Exercises:
- dispatch_step returns "completed" when a worker is available
- dispatch_step returns "skipped" when no worker is available
- project isolation: wrong-pool worker cannot claim
- task labels contain blueprint_id and autopilot flag
- MissionDriver uses fleet adapter when one is provided
- MissionDriver falls back to stub execution when no adapter
"""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.agent_graph import Agent, AgentKind
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.controller.fleet_adapter import FleetMissionAdapter
from sagewai.autopilot.mission import Mission
from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.registry import InMemoryFleetRegistry
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import NetworkPolicy, SandboxMode
from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    poll_timeout: float = 5.0,
) -> tuple[FleetMissionAdapter, InMemoryTaskStore]:
    """Return a (FleetMissionAdapter, InMemoryTaskStore) pair for testing."""
    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_interval=0.05, poll_timeout=poll_timeout)
    registry = InMemoryFleetRegistry()
    adapter = FleetMissionAdapter(
        dispatcher=dispatcher,
        registry=registry,
        poll_timeout=poll_timeout,
    )
    return adapter, store


def _make_mission(
    project_id: str = "test-project",
    bp: Blueprint | None = None,
) -> Mission:
    """Return a DRAFT mission backed by the given blueprint."""
    if bp is None:
        bp = make_synthetic_scheduled_blueprint()
    slots: dict = {"__blueprint_json__": bp.model_dump_json()}
    return Mission(
        mission_id="ms-fleet-test-001",
        project_id=project_id,
        blueprint_id=bp.id,
        blueprint_version=bp.version,
        slots=slots,
    )


def _schedule(mission: Mission) -> Mission:
    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)
    return mission


def _make_agent(node_id: str = "scout") -> Agent:
    return Agent(id=node_id, kind=AgentKind.DETERMINISTIC)


# ---------------------------------------------------------------------------
# FleetMissionAdapter unit tests
# ---------------------------------------------------------------------------


class TestFleetMissionAdapterDispatchStep:
    """Tests for FleetMissionAdapter.dispatch_step."""

    @pytest.mark.asyncio
    async def test_dispatch_step_returns_completed_when_worker_available(self) -> None:
        """A worker registered on the correct pool yields a completed step."""
        adapter, store = _make_adapter()
        mission = _make_mission(project_id="proj-alpha")
        agent = _make_agent("scout")

        # Pre-seed a matching task so the dispatcher can find it immediately.
        # The adapter._enqueue will add the real task; here we confirm the
        # full round-trip works end-to-end.
        result = await adapter.dispatch_step(agent, mission, {})

        assert result.status == "completed"
        assert result.node_id == "scout"
        assert result.output_preview is not None

    @pytest.mark.asyncio
    async def test_dispatch_step_output_preview_mentions_pool(self) -> None:
        """Completed step output_preview identifies the pool (project_id)."""
        adapter, store = _make_adapter()
        mission = _make_mission(project_id="proj-beta")
        agent = _make_agent("summarizer")

        result = await adapter.dispatch_step(agent, mission, {})

        assert "proj-beta" in (result.output_preview or "")

    @pytest.mark.asyncio
    async def test_dispatch_step_no_worker_returns_skipped(self) -> None:
        """When no worker claims the task within poll_timeout, returns skipped."""
        # Use a very short poll_timeout so the test doesn't hang.
        store = InMemoryTaskStore()
        dispatcher = FleetDispatcher(store=store, poll_interval=0.05, poll_timeout=0.1)
        registry = InMemoryFleetRegistry()
        adapter = FleetMissionAdapter(dispatcher=dispatcher, registry=registry, poll_timeout=0.1)

        # Use a project_id that no worker will match because the store is empty
        # AND the pool won't match any pre-seeded tasks.
        mission = _make_mission(project_id="empty-pool")
        # Override store to be empty so no task is ever matched.
        # The adapter enqueues into the same store, but we patch claim_task
        # to always return None to simulate a pool mismatch.
        original_claim = store.claim_task

        async def _never_claim(**kwargs):  # type: ignore[no-untyped-def]
            return None

        store.claim_task = _never_claim  # type: ignore[method-assign]

        agent = _make_agent("scout")
        result = await adapter.dispatch_step(agent, mission, {})

        assert result.status == "skipped"
        assert result.node_id == "scout"
        assert "No fleet worker available" in (result.output_preview or "")

    @pytest.mark.asyncio
    async def test_dispatch_step_task_labels_include_blueprint_id(self) -> None:
        """Enqueued task carries blueprint_id and autopilot=true in labels."""
        adapter, store = _make_adapter()
        mission = _make_mission(project_id="proj-gamma")
        agent = _make_agent("scout")

        # Intercept what gets enqueued.
        captured: list[dict] = []
        original_enqueue = store.enqueue

        def _capture(task: dict) -> None:
            captured.append(task)
            original_enqueue(task)

        store.enqueue = _capture  # type: ignore[method-assign]

        await adapter.dispatch_step(agent, mission, {})

        assert len(captured) == 1
        labels = captured[0].get("labels", {})
        assert labels.get("autopilot") == "true"
        assert labels.get("blueprint_id") == mission.blueprint_id

    @pytest.mark.asyncio
    async def test_dispatch_step_task_pool_equals_project_id(self) -> None:
        """Task pool is set to mission.project_id for project-scoped routing."""
        adapter, store = _make_adapter()
        mission = _make_mission(project_id="proj-delta")
        agent = _make_agent("scout")

        captured: list[dict] = []
        original_enqueue = store.enqueue

        def _capture(task: dict) -> None:
            captured.append(task)
            original_enqueue(task)

        store.enqueue = _capture  # type: ignore[method-assign]

        await adapter.dispatch_step(agent, mission, {})

        assert captured[0]["pool"] == "proj-delta"

    @pytest.mark.asyncio
    async def test_project_isolation_wrong_pool_cannot_claim(self) -> None:
        """A task for project A cannot be claimed by a worker on project B's pool.

        The InMemoryTaskStore enforces pool equality. We enqueue a task for
        'proj-finance' and try to claim it as 'proj-healthcare'. It must fail.
        """
        store = InMemoryTaskStore()
        # Task belongs to finance project.
        store.enqueue({
            "run_id": "r-isolation-test",
            "model": "medium",
            "pool": "proj-finance",
            "labels": {
                "autopilot": "true",
                "blueprint_id": "SYNTHETIC_scheduled_research",
                "project_id": "proj-finance",
            },
            "payload": {"node_id": "scout"},
            "requires_sandbox_mode": SandboxMode.NONE,
            "requires_image": f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}",
            "requires_network_policy": NetworkPolicy.NONE,
        })

        # Worker from healthcare project tries to claim.
        task = await store.claim_task(
            worker_id="healthcare-worker-1",
            org_id="org1",
            models_canonical=["medium"],
            pool="proj-healthcare",  # wrong pool
            labels={"project_id": "proj-healthcare"},
        )

        assert task is None, "Cross-project claim must be rejected by pool mismatch"

    @pytest.mark.asyncio
    async def test_project_isolation_correct_pool_can_claim(self) -> None:
        """A task for project A IS claimable by a worker on project A's pool."""
        store = InMemoryTaskStore()
        store.enqueue({
            "run_id": "r-isolation-ok",
            "model": "medium",
            "pool": "proj-finance",
            "labels": {
                "autopilot": "true",
                "blueprint_id": "SYNTHETIC_scheduled_research",
                "project_id": "proj-finance",
            },
            "payload": {"node_id": "scout"},
            "requires_sandbox_mode": SandboxMode.NONE,
            "requires_image": f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}",
            "requires_network_policy": NetworkPolicy.NONE,
        })

        task = await store.claim_task(
            worker_id="finance-worker-1",
            org_id="org1",
            models_canonical=["medium"],
            pool="proj-finance",
            labels={"project_id": "proj-finance", "autopilot": "true", "blueprint_id": "SYNTHETIC_scheduled_research"},
        )

        assert task is not None
        assert task["run_id"] == "r-isolation-ok"


# ---------------------------------------------------------------------------
# MissionDriver + FleetMissionAdapter integration tests
# ---------------------------------------------------------------------------


class TestMissionDriverFleetIntegration:
    """Tests for MissionDriver wired with FleetMissionAdapter."""

    @pytest.mark.asyncio
    async def test_driver_uses_fleet_adapter_when_provided(self) -> None:
        """Driver dispatches via fleet adapter when one is set."""
        adapter, store = _make_adapter()
        driver = MissionDriver(fleet_adapter=adapter)

        mission = _make_mission(project_id="proj-fleet-run")
        _schedule(mission)

        result = await driver.execute(mission)

        assert result.status == "completed"
        # Steps should use fleet output format, not stub format.
        for step in result.steps:
            assert "[stub]" not in (step.output_preview or "")
            assert "[fleet]" in (step.output_preview or "")

    @pytest.mark.asyncio
    async def test_driver_fleet_result_has_correct_step_count(self) -> None:
        """Fleet-dispatched run produces one step per linear node."""
        adapter, store = _make_adapter()
        driver = MissionDriver(fleet_adapter=adapter)

        # scheduled blueprint has 2 nodes (scout → summarizer)
        mission = _make_mission(project_id="proj-step-count")
        _schedule(mission)

        result = await driver.execute(mission)

        assert len(result.steps) == 2
        assert result.steps[0].node_id == "scout"
        assert result.steps[1].node_id == "summarizer"

    @pytest.mark.asyncio
    async def test_driver_falls_back_to_stub_when_no_adapter(self) -> None:
        """Without a fleet_adapter, driver produces stub steps."""
        driver = MissionDriver()  # no fleet_adapter

        mission = _make_mission()
        _schedule(mission)

        result = await driver.execute(mission)

        assert result.status == "completed"
        for step in result.steps:
            assert step.output_preview is not None

    @pytest.mark.asyncio
    async def test_driver_fleet_mission_transitions_to_completed(self) -> None:
        """Fleet-dispatched mission ends in COMPLETED state."""
        adapter, store = _make_adapter()
        driver = MissionDriver(fleet_adapter=adapter)

        mission = _make_mission(project_id="proj-state-check")
        _schedule(mission)

        await driver.execute(mission)

        assert mission.state == MissionState.COMPLETED

    @pytest.mark.asyncio
    async def test_driver_fleet_mission_id_in_result(self) -> None:
        """Result carries the mission_id from the original mission."""
        adapter, store = _make_adapter()
        driver = MissionDriver(fleet_adapter=adapter)

        mission = _make_mission(project_id="proj-id-check")
        _schedule(mission)

        result = await driver.execute(mission)

        assert result.mission_id == mission.mission_id


# ---------------------------------------------------------------------------
# Blueprint.sandbox_requirements pass-through tests (Task 17)
# ---------------------------------------------------------------------------


class TestBlueprintSandboxRequirementsPassThrough:
    """Verify that Blueprint.sandbox_requirements flows into the fleet task dict."""

    @pytest.mark.asyncio
    async def test_sandbox_requirements_none_by_default(self) -> None:
        """Existing blueprints without sandbox_requirements default to None."""
        bp = make_synthetic_scheduled_blueprint()
        assert bp.sandbox_requirements is None

    @pytest.mark.asyncio
    async def test_sandbox_requirements_stored_in_blueprint(self) -> None:
        """A Blueprint with sandbox_requirements round-trips correctly."""
        from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
        from sagewai.sandbox.resolution import SandboxRequirements

        base_bp = make_synthetic_scheduled_blueprint()
        reqs = SandboxRequirements(
            sandbox_mode=SandboxMode.PER_RUN,
            image="ghcr.io/sagewai/sandbox-ml:0.1.5",
            variant=SandboxImageVariant.ML,
            network_policy=NetworkPolicy.FULL,
        )
        bp = Blueprint.model_validate(
            base_bp.model_dump(mode="python") | {"sandbox_requirements": reqs}
        )
        assert bp.sandbox_requirements == reqs

        # Round-trip through JSON (as used by MissionDriver._walk_graph)
        restored = Blueprint.model_validate_json(bp.model_dump_json())
        assert restored.sandbox_requirements == reqs

    @pytest.mark.asyncio
    async def test_task_carries_sandbox_fields_when_blueprint_has_requirements(self) -> None:
        """Enqueued fleet task dict contains requires_sandbox_mode/image/network_policy."""
        from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
        from sagewai.sandbox.resolution import SandboxRequirements

        reqs = SandboxRequirements(
            sandbox_mode=SandboxMode.PER_RUN,
            image="ghcr.io/sagewai/sandbox-ml:0.1.5",
            variant=SandboxImageVariant.ML,
            network_policy=NetworkPolicy.FULL,
        )
        base_bp = make_synthetic_scheduled_blueprint()
        bp = Blueprint.model_validate(
            base_bp.model_dump(mode="python") | {"sandbox_requirements": reqs}
        )

        adapter, store = _make_adapter()
        mission = _make_mission(project_id="proj-sandbox-test", bp=bp)
        agent = _make_agent("scout")

        captured: list[dict] = []
        original_enqueue = store.enqueue

        def _capture(task: dict) -> None:
            captured.append(task)
            original_enqueue(task)

        store.enqueue = _capture  # type: ignore[method-assign]

        await adapter.dispatch_step(agent, mission, {})

        assert len(captured) == 1
        task = captured[0]
        # Plan 3b-i: sandbox fields are stored as serialisable strings (enum .value)
        assert task["requires_sandbox_mode"] == SandboxMode.PER_RUN.value
        assert task["requires_image"] == "ghcr.io/sagewai/sandbox-ml:0.1.5"
        assert task["requires_network_policy"] == NetworkPolicy.FULL.value

    @pytest.mark.asyncio
    async def test_task_carries_none_sandbox_fields_when_blueprint_has_no_requirements(
        self,
    ) -> None:
        """Enqueued task has None sandbox fields when blueprint.sandbox_requirements is None."""
        adapter, store = _make_adapter()
        # Default blueprint has no sandbox_requirements
        mission = _make_mission(project_id="proj-no-sandbox")
        agent = _make_agent("scout")

        captured: list[dict] = []
        original_enqueue = store.enqueue

        def _capture(task: dict) -> None:
            captured.append(task)
            original_enqueue(task)

        store.enqueue = _capture  # type: ignore[method-assign]

        await adapter.dispatch_step(agent, mission, {})

        assert len(captured) == 1
        task = captured[0]
        assert task["requires_sandbox_mode"] is None
        assert task["requires_image"] is None
        assert task["requires_network_policy"] is None

    @pytest.mark.asyncio
    async def test_admin_override_takes_precedence_over_blueprint(
        self, tmp_path, monkeypatch
    ) -> None:
        """When admin-state has an override for the entry agent, it replaces Blueprint."""
        import json

        from sagewai.sandbox.models import (
            NetworkPolicy,
            SandboxImageVariant,
            SandboxMode,
        )
        from sagewai.sandbox.resolution import SandboxRequirements

        # Seed admin-state with an override for "scout" (entry node of synthetic blueprint)
        state_file = tmp_path / "admin-state.json"
        state_file.write_text(json.dumps({
            "agents": [{
                "name": "scout",
                "sandbox_requirements_override": {
                    "sandbox_mode": "per_run",
                    "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
                    "network_policy": "full",
                    "required_secret_scopes": [],
                },
            }]
        }))
        monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

        # Build a blueprint with weaker requirements than the admin override
        base_bp = make_synthetic_scheduled_blueprint()
        weak_reqs = SandboxRequirements(
            sandbox_mode=SandboxMode.NONE,
            image="ghcr.io/sagewai/sandbox-base:1.0",
            variant=SandboxImageVariant.BASE,
            network_policy=NetworkPolicy.NONE,
        )
        bp = Blueprint.model_validate(
            base_bp.model_dump(mode="python") | {"sandbox_requirements": weak_reqs}
        )

        adapter, _store = _make_adapter()
        result = await adapter._extract_sandbox_requirements(bp)

        # Admin override values must win, not Blueprint
        assert result["requires_sandbox_mode"] == "per_run"
        assert result["requires_image"] == "ghcr.io/sagewai/sandbox-ml:0.1.5"
        assert result["requires_network_policy"] == "full"
