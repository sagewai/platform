# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Curator — background service that turns completed missions into training data.

The :class:`Curator` is stateful but synchronous. Typical usage::

    curator = Curator()
    # after each mission run:
    added = curator.process(result, blueprint, context)
    # periodically drain the job queue:
    jobs = curator.clear_pending_jobs()
    for job in jobs:
        schedule_fine_tune(job)  # caller's responsibility

Pass a :class:`~sagewai.autopilot.curator.fine_tune.FineTuneExecutor` at
construction time to have the Curator execute jobs inline when a dataset
threshold is crossed::

    from sagewai.autopilot.curator import Curator, FineTuneExecutor, FineTuneConfig
    executor = FineTuneExecutor(config=FineTuneConfig(output_dir="/models"))
    curator = Curator(executor=executor)
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import TYPE_CHECKING, Any

from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller import MissionRunResult

from .filter import eval_quality_filter
from .types import CuratorConfig, DatasetFormat, FineTuneJob, TrainingDataset

if TYPE_CHECKING:
    from .fine_tune import FineTuneExecutor

logger = logging.getLogger(__name__)


class Curator:
    """Background service that converts completed mission runs to training data.

    Attributes:
        config: Immutable configuration injected at construction.
        datasets: Mutable dict of resolved-dataset-id → :class:`TrainingDataset`.
        pending_jobs: Accumulated :class:`FineTuneJob` objects waiting to be
            consumed by the caller via :meth:`clear_pending_jobs`.
    """

    def __init__(
        self,
        config: CuratorConfig | None = None,
        executor: FineTuneExecutor | None = None,
    ) -> None:
        self.config: CuratorConfig = config or CuratorConfig()
        self.executor: FineTuneExecutor | None = executor
        self.datasets: dict[str, TrainingDataset] = {}
        self.pending_jobs: list[FineTuneJob] = []
        self._seen_mission_ids: set[str] = set()
        # Tracks per-dataset how many samples were present when the last
        # fine-tune job was enqueued, to avoid re-enqueuing on every
        # subsequent sample.
        self._last_job_threshold_hit: dict[str, int] = {}
        self._lock = threading.RLock()

    # ── Public API ─────────────────────────────────────────────────

    def process(
        self,
        result: MissionRunResult,
        blueprint: Blueprint,
        context: dict[str, Any],
    ) -> list[str]:
        """Process one completed mission run result against a blueprint.

        Args:
            result: The completed (or failed) run result from
                :class:`MissionDriver`.
            blueprint: The blueprint that generated the mission.
            context: Run-level metadata for quality filter evaluation.
                Keys expected by hooks: ``user_rating``, ``human_override``,
                ``reviewer_accepted``, ``project_id``, etc.

        Returns:
            List of dataset IDs that received a new sample this call.
            Empty list if no hooks passed their quality filter.
        """
        with self._lock:
            if self.config.deduplicate_by_mission_id:
                if result.mission_id in self._seen_mission_ids:
                    return []
                self._seen_mission_ids.add(result.mission_id)

            added: list[str] = []
            project_id: str = context.get("project_id", "default")

            for hook in blueprint.training_data_hooks:
                if not eval_quality_filter(hook.quality_filter, context):
                    continue

                dataset_id = self._resolve_dataset_name(hook.dataset, context)
                sample = self._build_sample(result, hook.format)  # type: ignore[arg-type]
                self._append_sample(dataset_id, project_id, hook.format, sample)  # type: ignore[arg-type]
                added.append(dataset_id)

                # Check fine-tune trigger
                if blueprint.learning_loop_target is not None:
                    self._maybe_enqueue_job(
                        dataset_id=dataset_id,
                        project_id=project_id,
                        loop_config=blueprint.learning_loop_target,
                    )

            return added

    def dataset_sample_count(self, dataset_id: str) -> int:
        """Return the number of samples in a dataset, or 0 if unknown."""
        ds = self.datasets.get(dataset_id)
        return ds.sample_count if ds is not None else 0

    def clear_pending_jobs(self) -> list[FineTuneJob]:
        """Pop and return all pending fine-tune jobs, clearing the queue."""
        with self._lock:
            jobs = list(self.pending_jobs)
            self.pending_jobs = []
            return jobs

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _resolve_dataset_name(template: str, context: dict[str, Any]) -> str:
        """Expand ``{project_id}`` and similar tokens in the dataset name."""
        try:
            return template.format(**context)
        except KeyError:
            # If template vars are missing, use the raw template string.
            return template

    @staticmethod
    def _build_sample(result: MissionRunResult, fmt: DatasetFormat) -> dict[str, Any]:
        """Build a training sample dict from a run result.

        Prefers ``step.output`` (full LLM response) over
        ``step.output_preview`` (truncated). Falls back to preview for
        backward compatibility with steps emitted before the harness
        wiring landed (and for steps that ran the direct-litellm
        fallback path).

        The exact schema per format follows the conventions used by
        the admin training export endpoint (see ``admin/serve.py``).
        """
        def step_text(s):
            return s.output or s.output_preview or ""

        if fmt == "alpaca":
            return {
                "instruction": result.mission_id,
                "input": "",
                "output": " | ".join(step_text(s) for s in result.steps),
            }
        if fmt == "sharegpt":
            return {
                "conversations": [
                    {"from": "human", "value": result.mission_id},
                    {
                        "from": "gpt",
                        "value": " | ".join(step_text(s) for s in result.steps),
                    },
                ]
            }
        if fmt == "classification":
            return {
                "text": result.mission_id,
                "label": result.status,
            }
        # raw
        return {
            "mission_id": result.mission_id,
            "status": result.status,
            "steps": [s.model_dump() for s in result.steps],
            "duration_seconds": result.duration_seconds,
        }

    def _append_sample(
        self,
        dataset_id: str,
        project_id: str,
        fmt: DatasetFormat,
        sample: dict[str, Any],
    ) -> None:
        """Append a sample to the named dataset, creating it if needed.

        Must be called while ``self._lock`` is held.
        """
        if dataset_id not in self.datasets:
            self.datasets[dataset_id] = TrainingDataset(
                dataset_id=dataset_id,
                project_id=project_id,
                format=fmt,
                samples=[sample],
            )
        else:
            existing = self.datasets[dataset_id]
            # Pydantic frozen — rebuild with extended sample list.
            self.datasets[dataset_id] = existing.model_copy(
                update={"samples": [*existing.samples, sample]}
            )

    def _maybe_enqueue_job(
        self,
        dataset_id: str,
        project_id: str,
        loop_config: Any,  # LearningLoopConfig
    ) -> None:
        """Enqueue a FineTuneJob if the dataset has reached the threshold.

        A job is enqueued exactly once per threshold crossing — further
        samples beyond the threshold do not produce additional jobs.

        Must be called while ``self._lock`` is held.
        """
        count = self.dataset_sample_count(dataset_id)
        threshold = loop_config.trigger_after_labeled_samples
        last_hit = self._last_job_threshold_hit.get(dataset_id, 0)

        # Enqueue only on the exact crossing point (or first time >= threshold)
        if count >= threshold and last_hit < threshold:
            self._last_job_threshold_hit[dataset_id] = count
            job = FineTuneJob(
                job_id=str(uuid.uuid4()),
                dataset_id=dataset_id,
                base_model=loop_config.base_model,
                project_id=project_id,
                method=loop_config.fine_tune_method,
                deploy_as=loop_config.deploy_as,
                status="pending",
            )

            if self.executor is not None:
                # Execute inline and update job status from result.
                dataset = self.datasets.get(dataset_id)
                if dataset is not None:
                    result = self.executor.execute(job, dataset)
                    logger.info(
                        "FineTuneExecutor returned status=%s for job %s",
                        result.status,
                        job.job_id,
                    )
                    job = job.model_copy(
                        update={
                            "status": result.status,
                            "error": result.reason if result.status == "failed" else None,
                        }
                    )

            self.pending_jobs.append(job)
