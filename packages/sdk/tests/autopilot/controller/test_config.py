# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ControllerConfig validation and integration with AutopilotController."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot.controller.controller import AutopilotController
from sagewai.autopilot.controller.types import ControllerConfig
from sagewai.autopilot.validators import default_registry

# ── ControllerConfig construction ──────────────────────────────────


def test_config_default_project_id():
    cfg = ControllerConfig()
    assert cfg.project_id == "default"


def test_config_custom_project_id():
    cfg = ControllerConfig(project_id="acme-prod")
    assert cfg.project_id == "acme-prod"


def test_config_requires_non_empty_project_id():
    with pytest.raises(Exception):
        ControllerConfig(project_id="")


def test_config_default_slots_empty_by_default():
    cfg = ControllerConfig()
    assert cfg.default_slots == {}


def test_config_accepts_default_slots():
    cfg = ControllerConfig(default_slots={"schedule": "0 9 * * 1-5"})
    assert cfg.default_slots["schedule"] == "0 9 * * 1-5"


def test_config_uses_default_registry_by_default():
    cfg = ControllerConfig()
    assert cfg.registry is not None


def test_config_accepts_custom_registry():
    reg = default_registry
    cfg = ControllerConfig(registry=reg)
    assert cfg.registry is reg


def test_config_is_immutable():
    cfg = ControllerConfig(project_id="test")
    with pytest.raises(Exception):
        cfg.project_id = "mutated"  # type: ignore[misc]


# ── default_slots merging ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_slots_merged_into_mission_slots():
    """default_slots in config are available in the created mission's slot dict."""
    from sagewai.autopilot.routing.types import AutoRouted, RankedBlueprint
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    bp_json = bp.model_dump_json()
    ranked = RankedBlueprint(blueprint_json=bp_json, score=0.93)
    auto_routed = AutoRouted(
        ranked=ranked,
        slots={"vendors": ["https://example.com"]},
        preview="Plan preview",
    )

    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value=auto_routed)

    config = ControllerConfig(
        project_id="test",
        default_slots={"schedule": "0 9 * * 1-5"},
    )
    ctrl = AutopilotController(
        router=mock_router,
        client=MagicMock(),
        config=config,
    )

    result = await ctrl.start_mission("research AI vendors daily")
    assert result.kind == "auto_routed"
    assert "schedule" in result.mission.slots
    assert result.mission.slots["schedule"] == "0 9 * * 1-5"


@pytest.mark.asyncio
async def test_extracted_slots_override_default_slots():
    """Extracted slots take precedence over default_slots."""
    from sagewai.autopilot.routing.types import AutoRouted, RankedBlueprint
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    ranked = RankedBlueprint(blueprint_json=bp.model_dump_json(), score=0.91)
    auto_routed = AutoRouted(
        ranked=ranked,
        slots={
            "vendors": ["https://example.com"],
            "schedule": "0 18 * * 5",  # overrides default
        },
        preview="Plan preview",
    )

    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value=auto_routed)

    config = ControllerConfig(
        project_id="test",
        default_slots={"schedule": "0 9 * * 1-5"},
    )
    ctrl = AutopilotController(
        router=mock_router,
        client=MagicMock(),
        config=config,
    )

    result = await ctrl.start_mission("research AI vendors")
    assert result.mission.slots["schedule"] == "0 18 * * 5"


# ── project_id isolation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mission_carries_controller_project_id():
    from sagewai.autopilot.routing.types import AutoRouted, RankedBlueprint
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    ranked = RankedBlueprint(blueprint_json=bp.model_dump_json(), score=0.95)
    auto_routed = AutoRouted(
        ranked=ranked,
        slots={"vendors": ["https://example.com"], "schedule": "0 9 * * 1-5"},
        preview="Plan preview",
    )

    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value=auto_routed)

    config = ControllerConfig(project_id="finance-team")
    ctrl = AutopilotController(
        router=mock_router,
        client=MagicMock(),
        config=config,
    )

    result = await ctrl.start_mission("research AI vendors")
    assert result.mission.project_id == "finance-team"
