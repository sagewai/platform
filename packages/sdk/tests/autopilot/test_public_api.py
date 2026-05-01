# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Smoke test that the public autopilot API is importable and complete."""

from __future__ import annotations


def test_public_surface_is_importable():
    from sagewai.autopilot import (  # noqa: F401
        Agent,
        AgentGraph,
        AgentGraphError,
        AgentKind,
        AutopilotError,
        Blueprint,
        BlueprintValidationError,
        Branch,
        EvalRef,
        LearningLoopConfig,
        Metric,
        Mission,
        MissionLifecycleError,
        MissionState,
        Mode,
        Operator,
        ProviderRequirement,
        SlotSpec,
        SlotValidationError,
        TrainingHook,
        ValidatorRegistry,
        default_registry,
    )


def test_public_all_lists_every_export():
    import sagewai.autopilot as ap

    for name in ap.__all__:
        assert hasattr(ap, name), f"{name} listed in __all__ but not exported"


def test_round_trip_synthetic_blueprints_through_public_api():
    from sagewai.autopilot import Blueprint

    from .fixtures import (
        make_synthetic_batch_blueprint,
        make_synthetic_event_driven_blueprint,
        make_synthetic_scheduled_blueprint,
    )

    for factory in (
        make_synthetic_scheduled_blueprint,
        make_synthetic_event_driven_blueprint,
        make_synthetic_batch_blueprint,
    ):
        original = factory()
        restored = Blueprint.model_validate_json(original.model_dump_json())
        assert restored == original
