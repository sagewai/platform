# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
"""Smoke tests for the dark-factory shared helpers (PR A).

Guards the multi-tenant foundation used by examples 27–30:

* bootstrap is idempotent and creates all four tenants
* Ollama preflight degrades gracefully without a local install
* fleet seeding registers dedicated + overflow workers with correct
  project_id labels
* the fleet scoreboard asserts isolation and renders a readable table
* the approval gate auto-approves in CI and records history
* training samples land in the per-tenant store and export to JSONL

Runs offline in under 2 seconds. No network, no GPU, no Ollama required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sagewai.examples._factory import (
    TENANTS,
    ApprovalGate,
    FleetScoreboard,
    GateDecision,
    TrainingSample,
    WorkItem,
    bootstrap,
    collect_sample,
    export_jsonl,
    ollama_preflight,
    seed_fleet,
)
from sagewai.examples._factory.train_tenant import reset as reset_training
from sagewai.fleet.registry import InMemoryFleetRegistry


# ── Bootstrap ────────────────────────────────────────────────────────


def test_bootstrap_creates_all_four_tenants(tmp_path: Path) -> None:
    state_path = tmp_path / "admin-state.json"
    report = bootstrap(state_path=state_path, run_preflight=False)

    assert report["state_path"] == str(state_path)
    assert set(report["tenants"]) == {t.slug for t in TENANTS}

    slugs = {p["slug"] for p in report["projects"]}
    assert slugs == {t.slug for t in TENANTS}

    data = json.loads(state_path.read_text())
    assert data["setup_complete"] is True
    assert len({p["slug"] for p in data["projects"]}) >= len(TENANTS)


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    state_path = tmp_path / "admin-state.json"
    first = bootstrap(state_path=state_path, run_preflight=False)
    second = bootstrap(state_path=state_path, run_preflight=False)

    assert first["tenants"] == second["tenants"]
    data = json.loads(state_path.read_text())
    counts: dict[str, int] = {}
    for project in data["projects"]:
        counts[project["slug"]] = counts.get(project["slug"], 0) + 1
    for spec in TENANTS:
        assert counts[spec.slug] == 1, f"duplicate project for {spec.slug}"


def test_bootstrap_applies_default_models(tmp_path: Path) -> None:
    state_path = tmp_path / "admin-state.json"
    bootstrap(state_path=state_path, run_preflight=False)
    data = json.loads(state_path.read_text())
    by_slug = {p["slug"]: p for p in data["projects"]}
    for spec in TENANTS:
        assert by_slug[spec.slug]["default_model"] == spec.default_model


# ── Ollama preflight ─────────────────────────────────────────────────


def test_preflight_does_not_raise_without_ollama() -> None:
    report = ollama_preflight(allow_cloud=False)
    assert isinstance(report.missing, list)
    assert isinstance(report.render(), str)


def test_preflight_allow_cloud_reports_ok_even_when_missing() -> None:
    report = ollama_preflight(allow_cloud=True)
    assert report.ok is True


# ── Fleet seeding ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seed_fleet_registers_dedicated_and_overflow() -> None:
    registry = InMemoryFleetRegistry()
    report = await seed_fleet(registry)

    assert "__overflow__" in report
    assert len(report["__overflow__"]) == 1
    for spec in TENANTS:
        assert report[spec.slug], f"no workers for {spec.slug}"
        assert len(report[spec.slug]) == spec.worker_count

    workers = await registry.list_workers(org_id="factory-demo")
    labels_by_slug: dict[str, set[str]] = {}
    for worker in workers:
        project_id = worker.capabilities.labels.get("project_id", "")
        labels_by_slug.setdefault(project_id, set()).add(worker.name)

    # Every dedicated worker carries its tenant's project_id label
    for spec in TENANTS:
        assert labels_by_slug.get(spec.slug), (
            f"no worker tagged project_id={spec.slug}"
        )
    # Overflow worker has no project_id label
    assert any(
        w.capabilities.labels.get("project_id") in (None, "")
        for w in workers
    ), "overflow worker must not carry a project_id"


# ── Work item ────────────────────────────────────────────────────────


def test_work_item_rejects_empty_tenant() -> None:
    with pytest.raises(ValueError):
        WorkItem(tenant="", channel="slack", brief="hi")


def test_work_item_metadata_includes_project_id() -> None:
    item = WorkItem(
        tenant="biz-ops",
        channel="cron",
        brief="reconcile stripe vs odoo",
    )
    meta = item.to_task_metadata()
    assert meta["project_id"] == "biz-ops"
    assert meta["work_item_id"] == item.id
    assert meta["brief"] == "reconcile stripe vs odoo"


# ── Fleet scoreboard ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scoreboard_renders_and_asserts_isolation() -> None:
    sb = FleetScoreboard(
        dedicated_workers={
            "build-1": "app-factory",
            "ops-1": "biz-ops",
        },
        overflow_workers={"overflow-default"},
    )
    async with sb:
        sb.record(worker_id="build-1", tenant="app-factory", duration_ms=120, cost_usd=0.001)
        sb.record(worker_id="build-1", tenant="app-factory", duration_ms=90, cost_usd=0.001)
        sb.record(worker_id="ops-1", tenant="biz-ops", duration_ms=80, cost_usd=0.002)
        sb.record(worker_id="overflow-default", tenant="wealth-desk", duration_ms=250, cost_usd=0.005)

    sb.assert_isolated()  # no cross-tenant leak from dedicated workers
    rendered = sb.render()
    assert "[app-factory]" in rendered
    assert "[biz-ops]" in rendered
    assert "[wealth-desk]" in rendered
    assert "overflow" in rendered


@pytest.mark.asyncio
async def test_scoreboard_raises_on_isolation_violation() -> None:
    sb = FleetScoreboard(
        dedicated_workers={"build-1": "app-factory"},
    )
    sb.record(worker_id="build-1", tenant="biz-ops", duration_ms=10)
    with pytest.raises(AssertionError):
        sb.assert_isolated()


# ── Approval gate ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_auto_approves_below_threshold() -> None:
    gate = ApprovalGate("app-factory", severity_threshold=3)
    outcome = await gate.check(
        work_item_id="wi-1",
        action="draft-pr",
        severity=2,
        summary="low-stakes",
    )
    assert outcome.decision == GateDecision.AUTO_APPROVED


@pytest.mark.asyncio
async def test_gate_calls_notifier_at_or_above_threshold() -> None:
    gate = ApprovalGate("wealth-desk", severity_threshold=3)
    outcome = await gate.check(
        work_item_id="wi-2",
        action="send-report",
        severity=4,
        summary="weekly wealth summary",
    )
    assert outcome.decision == GateDecision.AUTO_APPROVED  # mock notifier
    assert gate.history(), "gate should record history"


@pytest.mark.asyncio
async def test_gate_graduates_on_trust() -> None:
    gate = ApprovalGate(
        "school-mentor", severity_threshold=3, trust_threshold=80
    )
    gate.record_trust("send-parent-update", 90)
    outcome = await gate.check(
        work_item_id="wi-3",
        action="send-parent-update",
        severity=4,
        summary="weekly progress",
    )
    assert outcome.decision == GateDecision.GRADUATED


# ── Training flywheel ────────────────────────────────────────────────


def test_training_samples_are_tenant_scoped(tmp_path: Path) -> None:
    reset_training()
    collect_sample(
        TrainingSample(
            tenant="app-factory",
            agent_name="scaffold",
            model="qwen2.5-coder:7b",
            input_text="build a todo app",
            output_text="here is the repo layout…",
            quality=4,
        )
    )
    collect_sample(
        TrainingSample(
            tenant="biz-ops",
            agent_name="reconcile",
            model="llama3.1:8b",
            input_text="match invoices to payments",
            output_text="3 unmatched items found",
            quality=5,
        )
    )

    path_app = export_jsonl("app-factory", output_dir=tmp_path)
    path_ops = export_jsonl("biz-ops", output_dir=tmp_path)

    app_rows = path_app.read_text().strip().splitlines()
    ops_rows = path_ops.read_text().strip().splitlines()
    assert len(app_rows) == 1
    assert len(ops_rows) == 1

    # Cross-tenant isolation: each export contains only its tenant's rows
    assert "todo app" in app_rows[0]
    assert "invoices" in ops_rows[0]
    assert "todo app" not in ops_rows[0]
    assert "invoices" not in app_rows[0]
