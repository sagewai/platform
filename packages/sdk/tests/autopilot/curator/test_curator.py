# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the Curator background service."""

from __future__ import annotations

from typing import Any

from sagewai.autopilot.curator.curator import Curator
from sagewai.autopilot.curator.types import CuratorConfig, FineTuneJob
from tests.autopilot.fixtures import (
    make_synthetic_scheduled_blueprint,
)

from .conftest import make_run_result

# ── Construction ───────────────────────────────────────────────────


def test_curator_constructs_with_defaults():
    c = Curator()
    assert c.datasets == {}
    assert c.pending_jobs == []


def test_curator_constructs_with_custom_config():
    cfg = CuratorConfig(max_queue_size=50, deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    assert c.config.max_queue_size == 50


# ── process — basic filter pass/fail ──────────────────────────────


def test_process_passes_filter_appends_sample(scheduled_bp):
    c = Curator()
    result = make_run_result(mission_id="m-001")
    ctx: dict[str, Any] = {"user_rating": 5}
    added = c.process(result, scheduled_bp, ctx)
    assert len(added) == 1
    ds_id = added[0]
    assert ds_id in c.datasets
    assert c.datasets[ds_id].sample_count == 1


def test_process_fails_filter_does_not_append(scheduled_bp):
    c = Curator()
    result = make_run_result(mission_id="m-002")
    ctx: dict[str, Any] = {"user_rating": 2}
    added = c.process(result, scheduled_bp, ctx)
    assert added == []
    assert c.datasets == {}


def test_process_no_filter_always_appends():
    """A blueprint hook with quality_filter=None always passes."""
    from sagewai.autopilot.blueprint import Blueprint
    from sagewai.autopilot.models import TrainingHook
    from tests.autopilot.fixtures import make_synthetic_batch_blueprint

    bp = make_synthetic_batch_blueprint()
    no_filter_hook = TrainingHook(
        event="ingestor.completed",
        dataset="no_filter_ds",
        format="raw",
        quality_filter=None,
    )
    bp2 = Blueprint(**{**bp.model_dump(), "training_data_hooks": (no_filter_hook,)})
    c = Curator()
    result = make_run_result(mission_id="m-003")
    added = c.process(result, bp2, {})
    assert len(added) == 1


# ── process — deduplication ────────────────────────────────────────


def test_same_mission_id_deduplicated(scheduled_bp):
    c = Curator()
    result = make_run_result(mission_id="m-dup")
    ctx = {"user_rating": 5}
    c.process(result, scheduled_bp, ctx)
    added2 = c.process(result, scheduled_bp, ctx)
    assert added2 == []
    ds = list(c.datasets.values())[0]
    assert ds.sample_count == 1


def test_deduplication_disabled_allows_reprocessing(scheduled_bp):
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    result = make_run_result(mission_id="m-dup2")
    ctx = {"user_rating": 5}
    c.process(result, scheduled_bp, ctx)
    c.process(result, scheduled_bp, ctx)
    ds = list(c.datasets.values())[0]
    assert ds.sample_count == 2


# ── process — multiple hooks ───────────────────────────────────────


def test_two_hooks_with_different_filters():
    from sagewai.autopilot.blueprint import Blueprint
    from sagewai.autopilot.models import TrainingHook

    bp = make_synthetic_scheduled_blueprint()
    hook_a = TrainingHook(
        event="a.completed",
        dataset="dataset_a",
        format="alpaca",
        quality_filter="rating >= 3",
    )
    hook_b = TrainingHook(
        event="b.completed",
        dataset="dataset_b",
        format="sharegpt",
        quality_filter="rating >= 5",
    )
    bp2 = Blueprint(**{**bp.model_dump(), "training_data_hooks": (hook_a, hook_b)})
    c = Curator()
    result = make_run_result(mission_id="m-multi")
    ctx = {"rating": 4}  # passes A, fails B
    added = c.process(result, bp2, ctx)
    assert len(added) == 1
    assert "dataset_a" in c.datasets
    assert "dataset_b" not in c.datasets


# ── process — dataset template expansion ──────────────────────────


def test_dataset_name_project_id_expansion():
    from sagewai.autopilot.blueprint import Blueprint
    from sagewai.autopilot.models import TrainingHook

    bp = make_synthetic_scheduled_blueprint()
    hook = TrainingHook(
        event="x.done",
        dataset="ds_{project_id}",
        format="alpaca",
        quality_filter=None,
    )
    bp2 = Blueprint(**{**bp.model_dump(), "training_data_hooks": (hook,)})
    c = Curator()
    result = make_run_result(mission_id="m-tpl")
    ctx = {"project_id": "finance"}
    added = c.process(result, bp2, ctx)
    assert added == ["ds_finance"]
    assert "ds_finance" in c.datasets


# ── dataset_sample_count ───────────────────────────────────────────


def test_dataset_sample_count_returns_zero_for_unknown(scheduled_bp):
    c = Curator()
    assert c.dataset_sample_count("not-a-dataset") == 0


def test_dataset_sample_count_tracks_growth(scheduled_bp):
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    ctx = {"user_rating": 5}
    for i in range(5):
        c.process(make_run_result(mission_id=f"m-{i}"), scheduled_bp, ctx)
    ds_id = list(c.datasets.keys())[0]
    assert c.dataset_sample_count(ds_id) == 5


# ── FineTuneJob enqueuing ──────────────────────────────────────────


def test_job_enqueued_when_threshold_reached(event_driven_bp):
    """event_driven_bp has learning_loop_target with trigger=500."""
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    ctx = {"human_override": None}
    # Feed 499 runs — should not trigger
    for i in range(499):
        c.process(make_run_result(mission_id=f"m-{i}"), event_driven_bp, ctx)
    assert c.pending_jobs == []
    # Feed run 500 — should trigger exactly once
    c.process(make_run_result(mission_id="m-trigger"), event_driven_bp, ctx)
    assert len(c.pending_jobs) == 1
    job = c.pending_jobs[0]
    assert isinstance(job, FineTuneJob)
    assert job.base_model == event_driven_bp.learning_loop_target.base_model
    assert job.method == event_driven_bp.learning_loop_target.fine_tune_method
    assert job.status == "pending"


def test_job_not_enqueued_without_learning_loop_target(scheduled_bp):
    """scheduled_bp has no learning_loop_target."""
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    ctx = {"user_rating": 5}
    for i in range(1000):
        c.process(make_run_result(mission_id=f"m-{i}"), scheduled_bp, ctx)
    assert c.pending_jobs == []


def test_job_enqueued_only_once_at_threshold(event_driven_bp):
    """After the threshold job is enqueued, additional samples do not re-enqueue."""
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    ctx = {"human_override": None}
    for i in range(502):
        c.process(make_run_result(mission_id=f"m-{i}"), event_driven_bp, ctx)
    assert len(c.pending_jobs) == 1


# ── clear_pending_jobs ─────────────────────────────────────────────


def test_clear_pending_jobs_returns_and_clears(event_driven_bp):
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)
    ctx = {"human_override": None}
    for i in range(500):
        c.process(make_run_result(mission_id=f"m-{i}"), event_driven_bp, ctx)
    jobs = c.clear_pending_jobs()
    assert len(jobs) == 1
    assert c.pending_jobs == []


def test_clear_pending_jobs_empty_returns_empty_list():
    c = Curator()
    assert c.clear_pending_jobs() == []


# ── Thread-safety ─────────────────────────────────────────────────


def test_curator_process_concurrent_no_exceptions():
    """Concurrent calls to process() must not raise and must be consistent."""
    import concurrent.futures
    from typing import Any

    bp = make_synthetic_scheduled_blueprint()
    c = Curator(config=CuratorConfig(deduplicate_by_mission_id=False))

    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        result = make_run_result(mission_id=f"concurrent-{idx}")
        ctx: dict[str, Any] = {"user_rating": 5}
        try:
            c.process(result, bp, ctx)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_worker, i) for i in range(50)]
        concurrent.futures.wait(futures)

    assert errors == [], f"Exceptions during concurrent process(): {errors}"
    # At least some samples should have been recorded
    total = sum(ds.sample_count for ds in c.datasets.values())
    assert total == 50


def test_curator_clear_pending_jobs_concurrent_no_duplicates():
    """clear_pending_jobs() under concurrent writers must not lose or duplicate."""
    import concurrent.futures
    from typing import Any

    bp = make_synthetic_scheduled_blueprint()
    c = Curator(config=CuratorConfig(deduplicate_by_mission_id=False))

    def _add(idx: int) -> None:
        result = make_run_result(mission_id=f"cj-{idx}")
        ctx: dict[str, Any] = {"user_rating": 5}
        c.process(result, bp, ctx)

    # First populate some entries
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_add, i) for i in range(20)]
        concurrent.futures.wait(futures)

    # Concurrent clear calls must not raise
    errors: list[Exception] = []
    all_jobs: list[list] = []

    def _clear() -> None:
        try:
            all_jobs.append(c.clear_pending_jobs())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_clear) for _ in range(4)]
        concurrent.futures.wait(futures)

    assert errors == []
    # After clearing the queue should be empty
    assert c.pending_jobs == []


# ── _build_sample — output vs output_preview preference ─────────────────


def test_curator_alpaca_uses_full_step_output_when_available():
    """Curator.process emits Alpaca samples built from step.output, not the 200-char preview."""
    from sagewai.autopilot.controller.types import StepResult, MissionRunResult
    from sagewai.autopilot.curator.curator import Curator

    full_text = "A" * 1000
    steps = (
        StepResult(
            node_id="scout",
            status="completed",
            output_preview=full_text[:200],
            output=full_text,
        ),
    )
    result = MissionRunResult(
        mission_id="m-1",
        status="completed",
        steps=steps,
        duration_seconds=1.0,
    )
    sample = Curator._build_sample(result, "alpaca")
    assert sample["output"] == full_text


def test_curator_alpaca_falls_back_to_preview_when_no_full_output():
    """Backward compat: when step.output is None, Curator uses output_preview."""
    from sagewai.autopilot.controller.types import StepResult, MissionRunResult
    from sagewai.autopilot.curator.curator import Curator

    steps = (
        StepResult(
            node_id="scout",
            status="completed",
            output_preview="legacy preview",
            output=None,
        ),
    )
    result = MissionRunResult(
        mission_id="m-1",
        status="completed",
        steps=steps,
        duration_seconds=1.0,
    )
    sample = Curator._build_sample(result, "alpaca")
    assert sample["output"] == "legacy preview"


def test_curator_sharegpt_uses_full_step_output_when_available():
    """ShareGPT format: gpt turn uses full output not preview."""
    from sagewai.autopilot.controller.types import StepResult, MissionRunResult
    from sagewai.autopilot.curator.curator import Curator

    full_text = "B" * 800
    steps = (
        StepResult(
            node_id="scout",
            status="completed",
            output_preview=full_text[:200],
            output=full_text,
        ),
    )
    result = MissionRunResult(
        mission_id="m-1",
        status="completed",
        steps=steps,
        duration_seconds=1.0,
    )
    sample = Curator._build_sample(result, "sharegpt")
    assert sample["conversations"][1]["value"] == full_text
