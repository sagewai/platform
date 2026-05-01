# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for FineTuneExecutor, FineTuneResult, FineTuneConfig, and Curator integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sagewai.autopilot.curator.fine_tune import (
    FineTuneConfig,
    FineTuneExecutor,
    FineTuneResult,
    _export_dataset_to_jsonl,
)
from sagewai.autopilot.curator.types import FineTuneJob, TrainingDataset

from .conftest import make_run_result

# ── FineTuneConfig ─────────────────────────────────────────────────


def test_fine_tune_config_defaults():
    cfg = FineTuneConfig()
    assert cfg.output_dir == "/tmp/sagewai-finetune"
    assert cfg.lora_r == 16
    assert cfg.lora_alpha == 32
    assert cfg.epochs == 1
    assert cfg.batch_size == 4
    assert cfg.learning_rate == pytest.approx(2e-4)


def test_fine_tune_config_custom_values():
    cfg = FineTuneConfig(
        output_dir="/models",
        lora_r=8,
        lora_alpha=16,
        epochs=3,
        batch_size=2,
        learning_rate=1e-4,
    )
    assert cfg.output_dir == "/models"
    assert cfg.lora_r == 8
    assert cfg.epochs == 3


def test_fine_tune_config_is_frozen():
    cfg = FineTuneConfig()
    with pytest.raises(Exception):
        cfg.epochs = 10  # type: ignore[misc]


# ── FineTuneResult ─────────────────────────────────────────────────


def test_fine_tune_result_skipped():
    r = FineTuneResult(status="skipped", reason="unsloth not installed")
    assert r.status == "skipped"
    assert r.reason == "unsloth not installed"
    assert r.model_path is None
    assert r.metrics == {}


def test_fine_tune_result_completed():
    r = FineTuneResult(
        status="completed",
        model_path="/models/job-abc",
        metrics={"train_loss": 0.42, "sample_count": 10},
    )
    assert r.status == "completed"
    assert r.model_path == "/models/job-abc"
    assert r.metrics["train_loss"] == pytest.approx(0.42)
    assert r.reason is None


def test_fine_tune_result_failed():
    r = FineTuneResult(status="failed", reason="CUDA OOM")
    assert r.status == "failed"
    assert r.reason == "CUDA OOM"
    assert r.model_path is None


def test_fine_tune_result_is_frozen():
    r = FineTuneResult(status="skipped", reason="no gpu")
    with pytest.raises(Exception):
        r.status = "completed"  # type: ignore[misc]


# ── Dataset JSONL export ───────────────────────────────────────────


def test_export_dataset_to_jsonl_alpaca(tmp_path: Path):
    ds = TrainingDataset(
        dataset_id="ds-001",
        project_id="proj-a",
        format="alpaca",
        samples=[
            {"instruction": "Summarise", "input": "", "output": "Summary here"},
            {"instruction": "Translate", "input": "Hello", "output": "Bonjour"},
        ],
    )
    out = tmp_path / "export.jsonl"
    count = _export_dataset_to_jsonl(ds, out)
    assert count == 2
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["instruction"] == "Summarise"
    assert first["output"] == "Summary here"


def test_export_dataset_to_jsonl_raw(tmp_path: Path):
    ds = TrainingDataset(
        dataset_id="ds-raw",
        project_id="proj-b",
        format="raw",
        samples=[
            {"mission_id": "m-1", "status": "completed"},
            {"mission_id": "m-2", "status": "failed"},
        ],
    )
    out = tmp_path / "raw.jsonl"
    count = _export_dataset_to_jsonl(ds, out)
    assert count == 2
    lines = out.read_text().splitlines()
    assert json.loads(lines[1])["mission_id"] == "m-2"


def test_export_dataset_empty(tmp_path: Path):
    ds = TrainingDataset(
        dataset_id="ds-empty",
        project_id="proj-c",
        format="alpaca",
        samples=[],
    )
    out = tmp_path / "empty.jsonl"
    count = _export_dataset_to_jsonl(ds, out)
    assert count == 0
    assert out.read_text() == ""


# ── FineTuneExecutor — Unsloth not installed ───────────────────────


def _make_job(base_model: str = "llama3") -> FineTuneJob:
    return FineTuneJob(
        job_id="job-test-001",
        dataset_id="ds-001",
        base_model=base_model,
        project_id="proj-a",
    )


def _make_dataset() -> TrainingDataset:
    return TrainingDataset(
        dataset_id="ds-001",
        project_id="proj-a",
        format="alpaca",
        samples=[{"instruction": "Hello", "input": "", "output": "World"}],
    )


def test_executor_returns_skipped_when_unsloth_missing(monkeypatch: pytest.MonkeyPatch):
    """Unsloth not installed → status='skipped' with informative reason."""
    # Remove unsloth from sys.modules so import raises ImportError
    monkeypatch.setitem(sys.modules, "unsloth", None)  # type: ignore[arg-type]
    executor = FineTuneExecutor()
    result = executor.execute(_make_job(), _make_dataset())
    assert result.status == "skipped"
    assert "unsloth" in result.reason.lower()
    assert result.model_path is None


def test_executor_skipped_result_has_no_model_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "unsloth", None)  # type: ignore[arg-type]
    result = FineTuneExecutor().execute(_make_job(), _make_dataset())
    assert result.model_path is None
    assert result.metrics == {}


def test_executor_default_config():
    executor = FineTuneExecutor()
    assert executor.config.lora_r == 16
    assert executor.config.epochs == 1


def test_executor_custom_config():
    cfg = FineTuneConfig(output_dir="/custom", lora_r=4, epochs=2)
    executor = FineTuneExecutor(config=cfg)
    assert executor.config.output_dir == "/custom"
    assert executor.config.lora_r == 4


# ── FineTuneExecutor — Unsloth mocked as installed ─────────────────


def test_executor_calls_run_unsloth_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """When Unsloth is present (mocked), _run_unsloth should be called."""
    mock_unsloth = MagicMock()
    mock_flm = MagicMock()
    monkeypatch.setitem(sys.modules, "unsloth", mock_unsloth)
    mock_unsloth.FastLanguageModel = mock_flm

    expected_result = FineTuneResult(
        status="completed",
        model_path=str(tmp_path / "job-test-001"),
        metrics={"train_loss": 0.5, "sample_count": 1},
    )
    executor = FineTuneExecutor(config=FineTuneConfig(output_dir=str(tmp_path)))
    monkeypatch.setattr(executor, "_run_unsloth", lambda job, ds, flm: expected_result)

    result = executor.execute(_make_job(), _make_dataset())
    assert result.status == "completed"
    assert result.model_path is not None


def test_executor_returns_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """If _run_unsloth raises, status should be 'failed'."""
    mock_unsloth = MagicMock()
    monkeypatch.setitem(sys.modules, "unsloth", mock_unsloth)
    mock_unsloth.FastLanguageModel = MagicMock()

    executor = FineTuneExecutor(config=FineTuneConfig(output_dir=str(tmp_path)))

    def boom(job: object, ds: object, flm: object) -> FineTuneResult:
        raise RuntimeError("GPU out of memory")

    monkeypatch.setattr(executor, "_run_unsloth", boom)

    result = executor.execute(_make_job(), _make_dataset())
    assert result.status == "failed"
    assert "GPU out of memory" in result.reason


# ── Curator + FineTuneExecutor integration ─────────────────────────


def test_curator_without_executor_leaves_job_pending(event_driven_bp):
    """Without an executor, job stays in 'pending' status after threshold."""
    from sagewai.autopilot.curator.curator import Curator
    from sagewai.autopilot.curator.types import CuratorConfig

    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    c = Curator(config=cfg)

    # event_driven_bp has trigger_after_labeled_samples=500
    for i in range(500):
        c.process(make_run_result(mission_id=f"m-{i:04d}"), event_driven_bp, {"human_override": None})

    jobs = c.clear_pending_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "pending"


def test_curator_with_executor_calls_execute(
    event_driven_bp, monkeypatch: pytest.MonkeyPatch
):
    """When executor is provided and threshold crossed, execute() is called."""
    from sagewai.autopilot.curator.curator import Curator
    from sagewai.autopilot.curator.fine_tune import FineTuneExecutor
    from sagewai.autopilot.curator.types import CuratorConfig

    executed: list[str] = []

    class SpyExecutor(FineTuneExecutor):
        def execute(self, job: FineTuneJob, dataset: TrainingDataset) -> FineTuneResult:
            executed.append(job.job_id)
            return FineTuneResult(status="skipped", reason="unsloth not installed")

    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    executor = SpyExecutor()
    c = Curator(config=cfg, executor=executor)

    for i in range(500):
        c.process(make_run_result(mission_id=f"m-{i:04d}"), event_driven_bp, {"human_override": None})

    assert len(executed) == 1

    jobs = c.clear_pending_jobs()
    assert len(jobs) == 1
    # Job was executed inline — status reflects executor's result
    assert all(j.status in ("skipped", "completed", "failed") for j in jobs)


def test_curator_with_executor_updates_job_status_to_skipped(
    event_driven_bp, monkeypatch: pytest.MonkeyPatch
):
    """Executor returning 'skipped' → job in pending_jobs has status='skipped'."""
    from sagewai.autopilot.curator.curator import Curator
    from sagewai.autopilot.curator.fine_tune import FineTuneExecutor
    from sagewai.autopilot.curator.types import CuratorConfig

    monkeypatch.setitem(sys.modules, "unsloth", None)  # type: ignore[arg-type]
    cfg = CuratorConfig(deduplicate_by_mission_id=False)
    executor = FineTuneExecutor()
    c = Curator(config=cfg, executor=executor)

    for i in range(500):
        c.process(make_run_result(mission_id=f"m-{i:04d}"), event_driven_bp, {"human_override": None})

    jobs = c.clear_pending_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "skipped"


# ── Public API surface ─────────────────────────────────────────────


def test_public_api_exports_fine_tune_types():
    from sagewai.autopilot.curator import FineTuneConfig, FineTuneExecutor, FineTuneResult

    assert FineTuneExecutor is not None
    assert FineTuneResult is not None
    assert FineTuneConfig is not None
