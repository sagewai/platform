# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared fixtures for curator tests."""

from __future__ import annotations

import pytest

from sagewai.autopilot.controller import MissionRunResult, StepResult
from sagewai.autopilot.curator.types import CuratorConfig
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_event_driven_blueprint,
    make_synthetic_scheduled_blueprint,
)


@pytest.fixture()
def scheduled_bp():
    return make_synthetic_scheduled_blueprint()


@pytest.fixture()
def event_driven_bp():
    return make_synthetic_event_driven_blueprint()


@pytest.fixture()
def batch_bp():
    return make_synthetic_batch_blueprint()


def make_run_result(
    mission_id: str = "mission-001",
    status: str = "completed",
    duration: float = 1.2,
    error: str | None = None,
) -> MissionRunResult:
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        steps=(StepResult(node_id="summarizer", status="ok", output_preview="done"),),
        duration_seconds=duration,
        error=error,
    )


@pytest.fixture()
def default_config() -> CuratorConfig:
    return CuratorConfig()
