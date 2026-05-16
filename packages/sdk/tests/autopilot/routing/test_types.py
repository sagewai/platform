# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for RoutingResult discriminated union types."""

from __future__ import annotations

import pytest

from sagewai.autopilot.routing.types import (
    AutoRouted,
    PickerNeeded,
    RankedBlueprint,
    RoutingResult,
    SynthesisNeeded,
)


def _make_ranked(score: float = 0.9, bp_id: str = "bp-1") -> RankedBlueprint:
    return RankedBlueprint(blueprint_json=f'{{"id":"{bp_id}"}}', score=score)


# ── RankedBlueprint ────────────────────────────────────────────────


def test_ranked_blueprint_stores_fields():
    rb = _make_ranked(score=0.88, bp_id="research-01")
    assert rb.score == pytest.approx(0.88)
    assert '"id":"research-01"' in rb.blueprint_json


def test_ranked_blueprint_score_bounds():
    with pytest.raises(Exception):
        RankedBlueprint(blueprint_json='{"id":"x"}', score=1.1)
    with pytest.raises(Exception):
        RankedBlueprint(blueprint_json='{"id":"x"}', score=-0.01)


def test_ranked_blueprint_is_immutable():
    rb = _make_ranked()
    with pytest.raises(Exception):
        rb.score = 0.5  # type: ignore[misc]


# ── AutoRouted ─────────────────────────────────────────────────────


def test_auto_routed_kind():
    ar = AutoRouted(
        ranked=_make_ranked(0.92),
        slots={"topic": "AI"},
        preview="Plan: research AI\n- slot topic = AI",
    )
    assert ar.kind == "auto_routed"
    assert ar.slots == {"topic": "AI"}
    assert "AI" in ar.preview


def test_auto_routed_requires_non_empty_preview():
    with pytest.raises(Exception):
        AutoRouted(ranked=_make_ranked(), slots={}, preview="")


def test_auto_routed_is_immutable():
    ar = AutoRouted(ranked=_make_ranked(), slots={}, preview="ok")
    with pytest.raises(Exception):
        ar.kind = "hacked"  # type: ignore[misc]


# ── PickerNeeded ───────────────────────────────────────────────────


def test_picker_needed_kind():
    pn = PickerNeeded(candidates=(_make_ranked(0.80), _make_ranked(0.74), _make_ranked(0.68)))
    assert pn.kind == "picker_needed"
    assert len(pn.candidates) == 3


def test_picker_needed_preserves_order():
    candidates = (_make_ranked(0.82, "a"), _make_ranked(0.75, "b"), _make_ranked(0.66, "c"))
    pn = PickerNeeded(candidates=candidates)
    assert pn.candidates[0].score > pn.candidates[1].score > pn.candidates[2].score


def test_picker_needed_requires_at_least_one_candidate():
    with pytest.raises(Exception):
        PickerNeeded(candidates=())


# ── SynthesisNeeded ────────────────────────────────────────────────


def test_synthesis_needed_kind():
    sn = SynthesisNeeded(goal="build a Slack bot")
    assert sn.kind == "synthesis_needed"
    assert sn.goal == "build a Slack bot"


def test_synthesis_needed_requires_non_empty_goal():
    with pytest.raises(Exception):
        SynthesisNeeded(goal="")


# ── RoutingResult union ────────────────────────────────────────────


def test_routing_result_type_alias_covers_all_variants():
    ar: RoutingResult = AutoRouted(ranked=_make_ranked(), slots={}, preview="ok")
    pn: RoutingResult = PickerNeeded(candidates=(_make_ranked(),))
    sn: RoutingResult = SynthesisNeeded(goal="some goal")
    assert ar.kind == "auto_routed"
    assert pn.kind == "picker_needed"
    assert sn.kind == "synthesis_needed"


@pytest.mark.parametrize(
    "result",
    [
        AutoRouted(ranked=_make_ranked(), slots={"k": "v"}, preview="preview text"),
        PickerNeeded(candidates=(_make_ranked(0.77),)),
        SynthesisNeeded(goal="do the thing"),
    ],
)
def test_routing_result_variants_are_all_immutable(result: RoutingResult):
    with pytest.raises(Exception):
        result.kind = "mutated"  # type: ignore[misc]
