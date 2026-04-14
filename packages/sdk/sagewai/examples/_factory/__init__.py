# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared helpers for the Sagewai dark-factory examples (27–30).

Every factory example runs as an isolated tenant inside a single Sagewai
admin instance, shares one fleet of local runners, and independently
trains its own workforce. The helpers in this package provide the
multi-tenant envelope each example wraps around its own domain logic:

* ``bootstrap_tenants`` — idempotently creates the four tenant projects
  in admin state and seeds the shared fleet of runners.
* ``local_models`` — local-first model registry + Ollama preflight.
* ``work_item`` — the one dataclass every factory enqueues.
* ``approval_gate`` — human-in-the-loop checkpoint, with trust graduation.
* ``fleet_scoreboard`` — proves tenant isolation + prints a per-worker
  breakdown at the end of a run.
* ``train_tenant`` — per-tenant training flywheel (CI stub by default,
  real Unsloth behind a flag).

See ``.claude/plans/dark-factory-tenants.md`` for the full design doc.

This subpackage is intentionally importable from anywhere::

    from sagewai.examples._factory import (
        TENANTS,
        WorkItem,
        ApprovalGate,
        FleetScoreboard,
        bootstrap,
        seed_fleet,
        ollama_preflight,
    )
"""

from sagewai.examples._factory.approval_gate import ApprovalGate, GateDecision
from sagewai.examples._factory.bootstrap_tenants import (
    TENANTS,
    TenantSpec,
    bootstrap,
    ensure_project,
    seed_fleet,
)
from sagewai.examples._factory.fleet_scoreboard import FleetScoreboard, ScoreRow
from sagewai.examples._factory.local_models import (
    LOCAL_MODEL_REGISTRY,
    ModelTier,
    ollama_preflight,
    pull_hint,
)
from sagewai.examples._factory.train_tenant import (
    TrainingSample,
    collect_sample,
    export_jsonl,
    register_trained_tier,
    run_unsloth_stub,
)
from sagewai.examples._factory.work_item import WorkItem

__all__ = [
    "TENANTS",
    "LOCAL_MODEL_REGISTRY",
    "ApprovalGate",
    "FleetScoreboard",
    "GateDecision",
    "ModelTier",
    "ScoreRow",
    "TenantSpec",
    "TrainingSample",
    "WorkItem",
    "bootstrap",
    "collect_sample",
    "ensure_project",
    "export_jsonl",
    "ollama_preflight",
    "pull_hint",
    "register_trained_tier",
    "run_unsloth_stub",
    "seed_fleet",
]
