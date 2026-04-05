# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Worker configuration and routing models for distributed execution.

Defines the data models used by WorkflowWorker for pool/label-based
routing, per-worker credential injection, and load-balanced assignment.

Usage::

    from sagewai.models.worker import WorkerCredentials, RoutingConstraints
    from sagewai.models.inference import InferenceParams

    # Worker with Ollama credentials
    creds = WorkerCredentials(
        model_overrides={"default": "ollama/llama3.2"},
        inference_overrides=InferenceParams(
            api_base="http://localhost:11434",
        ),
    )

    # Route to a specific pool
    routing = RoutingConstraints(
        worker_pool="local-ollama",
        strategy=RoutingStrategy.DIRECT,
    )
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from sagewai.models.inference import InferenceParams


class RoutingStrategy(str, Enum):
    """Strategy for assigning workflow runs to workers.

    Strategies are evaluated at enqueue time for ``ROUND_ROBIN``,
    ``LEAST_LOADED``, and ``THRESHOLD``. ``DIRECT`` defers to
    claim-time filtering via pool/labels/worker_id.
    """

    DIRECT = "direct"
    """Explicit pool/labels/worker_id — workers self-select at claim time."""

    ROUND_ROBIN = "round_robin"
    """Rotate across eligible workers in a pool."""

    LEAST_LOADED = "least_loaded"
    """Pick the worker with the lowest active_runs / max_concurrent ratio."""

    THRESHOLD = "threshold"
    """Like least_loaded, but skip workers above the capacity threshold."""


class WorkerCredentials(BaseModel):
    """Credentials and model overrides injected by a worker at execution time.

    These credentials are set via a ``ContextVar`` in the worker process
    and read by ``UniversalAgent._build_litellm_kwargs()`` at LLM call time.
    Credentials never touch the database — they exist only in-process.

    Attributes:
        model_overrides: Maps logical model names to concrete model strings.
            The key ``"default"`` replaces the agent's ``config.model``.
        inference_overrides: LLM provider settings (api_base, api_key, etc.)
            that override the agent's ``config.inference`` at call time.
        env_overrides: Extra environment variables set during execution.
            Applied in-process only, never persisted.
    """

    model_overrides: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map logical names to model strings. " '"default" replaces agent config.model.'
        ),
    )
    inference_overrides: InferenceParams | None = Field(
        default=None,
        description="LLM provider overrides (api_base, api_key, etc.)",
    )
    env_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Extra env vars (in-process only, never persisted)",
    )


class RoutingConstraints(BaseModel):
    """Routing constraints attached to a workflow submission.

    Determines which worker(s) are eligible to claim the run and
    which load-balancing strategy to use.

    Attributes:
        worker_pool: Target pool name. Only workers in this pool can claim.
        worker_labels: Required label set. Worker must have all these labels
            (JSONB containment match).
        worker_id: Target a specific worker by ID.
        strategy: Load-balancing strategy. Defaults to LEAST_LOADED when
            no explicit constraints are set.
        capacity_threshold: For THRESHOLD strategy — skip workers whose
            load ratio exceeds this value (0.0–1.0).
    """

    worker_pool: str | None = Field(
        default=None,
        description="Target worker pool name",
    )
    worker_labels: dict[str, Any] | None = Field(
        default=None,
        description="Required worker labels (JSONB containment match)",
    )
    worker_id: str | None = Field(
        default=None,
        description="Target a specific worker by ID",
    )
    strategy: RoutingStrategy = Field(
        default=RoutingStrategy.LEAST_LOADED,
        description="Load-balancing strategy",
    )
    capacity_threshold: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="For THRESHOLD strategy: skip workers above this load ratio",
    )
