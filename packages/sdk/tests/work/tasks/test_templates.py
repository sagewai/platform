# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task templates, slots, and the built-in catalogue."""

from __future__ import annotations

import pytest

from sagewai.work.tasks.models import GateMode, RoleAlias, RuntimeRef, TaskKind
from sagewai.work.tasks.templates import (
    CATALOGUE,
    SlotSpec,
    SlotValidationError,
    TaskTemplate,
    default_registry,
    get_template,
    validate_slots,
)


def test_catalogue_ships_software_delivery_and_research_report() -> None:
    assert set(CATALOGUE) == {"software_delivery", "scheduled_research_report"}
    software = get_template("software_delivery")
    assert software.kind is TaskKind.BATCH and software.profile == "software"
    assert software.roles[RoleAlias.PLANNER] == (RuntimeRef.CLAUDE_ANALYSIS,)
    assert software.roles[RoleAlias.IMPLEMENTER] == (RuntimeRef.CODEX,)
    assert software.roles[RoleAlias.REVIEWER] == (RuntimeRef.CLAUDE_REVIEW,)
    assert software.roles[RoleAlias.ANALYST] == (
        RuntimeRef.HARNESS_MEDIUM,
        RuntimeRef.CLAUDE_ANALYSIS,
    )
    assert software.grant_level == "execute"
    assert software.authority_floor.plan is GateMode.REQUIRE
    report = get_template("scheduled_research_report")
    assert report.kind is TaskKind.SCHEDULED and report.profile == "report"
    assert report.default_cron == "0 8 * * *"
    assert report.roles[RoleAlias.COMPOSER] == (
        RuntimeRef.HARNESS_MEDIUM,
        RuntimeRef.CLAUDE_ANALYSIS,
    )
    assert len(report.plan_skeleton) == 1
    assert report.grant_level == "read"
    with pytest.raises(KeyError):
        get_template("event_triage")


def test_templates_are_versioned_and_have_example_goals() -> None:
    for template in CATALOGUE.values():
        assert template.version
        assert len(template.example_goals) >= 4
        assert template.required_slots.keys().isdisjoint(template.optional_slots.keys())


def test_validate_slots_applies_defaults_and_validators() -> None:
    report = get_template("scheduled_research_report")
    values = validate_slots(report, {"sources": ["https://example.com/blog"]}, default_registry)
    assert values["sources"] == ["https://example.com/blog"]
    assert values["cron"] == "0 8 * * *"
    with pytest.raises(SlotValidationError):
        validate_slots(report, {}, default_registry)
    with pytest.raises(SlotValidationError):
        validate_slots(report, {"sources": ["ftp://nope"]}, default_registry)
    with pytest.raises(SlotValidationError):
        validate_slots(report, {"sources": ["https://a.example"], "cron": "bad"}, default_registry)


def test_slot_spec_unknown_validator_is_an_error() -> None:
    spec = SlotSpec(type_="str", description="x", validator_name="missing")
    with pytest.raises(SlotValidationError):
        spec.validate_value("v", slot_name="x", registry=default_registry)


def test_template_rejects_empty_ladders_and_unknown_role_binding() -> None:
    with pytest.raises(ValueError):
        TaskTemplate(
            id="t",
            version="1",
            title="t",
            description="",
            category="c",
            kind=TaskKind.BATCH,
            profile="software",
            example_goals=("a", "b", "c", "d"),
            roles={RoleAlias.PLANNER: ()},
        )
