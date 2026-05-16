# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic worker capability matcher for autopilot fleet dispatch.

Ranks workers against an agent step's required tools and providers,
returning only eligible workers sorted by availability and queue depth.

Usage::

    from sagewai.autopilot.controller.fleet_match import match_workers
    from sagewai.fleet.models import WorkerRecord, WorkerCapabilities, WorkerApprovalStatus
    from datetime import datetime, timezone

    workers = [...]  # list[WorkerRecord]
    ranked = match_workers(agent, workers)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sagewai.fleet.models import WorkerRecord
from sagewai.fleet.normalizer import ModelNormalizer

if TYPE_CHECKING:
    from sagewai.autopilot.agent_graph import Agent


# Workers with this approval status are considered eligible for dispatch.
_APPROVED = "approved"


class NoWorkerAvailableError(Exception):
    """Raised when no worker satisfies an agent step's capability requirements.

    Attributes:
        agent_id: The agent node id that could not be matched.
        unmet_labels: Tool labels not covered by any worker.
        unmet_models: Canonical model names not covered by any worker.
    """

    def __init__(
        self,
        agent_id: str,
        unmet_labels: list[str],
        unmet_models: list[str],
    ) -> None:
        self.agent_id = agent_id
        self.unmet_labels = unmet_labels
        self.unmet_models = unmet_models
        super().__init__(
            f"no worker available for agent {agent_id!r}: "
            f"unmet labels={unmet_labels}, unmet models={unmet_models}"
        )


def match_workers(agent: Agent, pool: list[WorkerRecord]) -> list[WorkerRecord]:
    """Return eligible workers sorted by availability and queue depth.

    Eligibility rules:
    - Worker must have ``approval_status == "approved"``.
    - ``set(agent.tools) ⊆ set(worker.capabilities.labels.keys())``.
    - ``set(canonical(agent.providers_required)) ⊆ set(worker.capabilities.models_canonical)``.

    Sort key (ascending): ``(0 if idle else 1, queue_depth, worker.id)``.

    Args:
        agent: The agent node being dispatched.  Must have ``.tools``
            (tuple/list of str) and optionally ``.providers_required``
            (list of objects with a ``.name`` attribute, or list of str).
        pool: Snapshot of workers to rank against.

    Returns:
        Filtered, ranked list of :class:`WorkerRecord` objects.
    """
    required_tools: set[str] = set(agent.tools) if agent.tools else set()

    # Normalise providers_required to a list of canonical model name strings.
    raw_providers: list[str] = []
    if hasattr(agent, "providers_required") and agent.providers_required:
        for p in agent.providers_required:
            if isinstance(p, str):
                raw_providers.append(p)
            elif hasattr(p, "name"):
                raw_providers.append(p.name)
    required_models: set[str] = set(ModelNormalizer.canonical_list(raw_providers))

    eligible: list[WorkerRecord] = []
    for worker in pool:
        # Only approved workers can be dispatched.
        status = getattr(worker, "approval_status", None)
        if status is not None:
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val != _APPROVED:
                continue

        caps = worker.capabilities

        # Tool label check: every required tool must appear as a label key.
        worker_labels: set[str] = set(caps.labels.keys()) if caps.labels else set()
        if not required_tools.issubset(worker_labels):
            continue

        # Model check: canonical intersection.
        worker_models: set[str] = set(caps.models_canonical) if caps.models_canonical else set()
        if required_models and not required_models.issubset(worker_models):
            continue

        eligible.append(worker)

    def _sort_key(w: WorkerRecord) -> tuple[int, int, str]:
        probe = getattr(w, "probe_status", None) or ""
        is_busy = 1 if probe == "degraded" else 0
        queue_depth = getattr(w, "queue_depth", 0) or 0
        return (is_busy, queue_depth, w.id)

    eligible.sort(key=_sort_key)
    return eligible
