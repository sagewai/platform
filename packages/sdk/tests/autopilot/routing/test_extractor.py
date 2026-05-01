# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the slot extractor Protocol and rule-based stub."""

from __future__ import annotations

import inspect

import pytest

from sagewai.autopilot.routing.extractor import RuleBasedExtractor, SlotExtractor

# ── Protocol conformance ───────────────────────────────────────────


def test_rule_based_extractor_is_slot_extractor():
    """RuleBasedExtractor must satisfy the SlotExtractor Protocol at runtime."""
    assert issubclass(RuleBasedExtractor, SlotExtractor)


def test_slot_extractor_protocol_has_extract_method():
    members = {name for name, _ in inspect.getmembers(SlotExtractor)}
    assert "extract" in members


# ── RuleBasedExtractor.extract ────────────────────────────────────


@pytest.fixture()
def extractor() -> RuleBasedExtractor:
    return RuleBasedExtractor()


def test_extract_single_key_value(extractor: RuleBasedExtractor):
    result = extractor.extract("monitor topic=cybersecurity daily", slot_names=["topic"])
    assert result == {"topic": "cybersecurity"}


def test_extract_multiple_key_values(extractor: RuleBasedExtractor):
    goal = "schedule=0 9 * * 1-5 vendors=openai,anthropic limit=50"
    result = extractor.extract(goal, slot_names=["schedule", "vendors", "limit"])
    assert result["limit"] == "50"
    assert result["vendors"] == "openai,anthropic"


def test_extract_quoted_value(extractor: RuleBasedExtractor):
    goal = 'summarize topic="machine learning trends" depth=brief'
    result = extractor.extract(goal, slot_names=["topic", "depth"])
    assert result["topic"] == "machine learning trends"
    assert result["depth"] == "brief"


def test_extract_missing_slot_returns_none(extractor: RuleBasedExtractor):
    result = extractor.extract("do something", slot_names=["topic"])
    assert result == {"topic": None}


def test_extract_unknown_keys_ignored(extractor: RuleBasedExtractor):
    goal = "topic=AI noise=garbage"
    result = extractor.extract(goal, slot_names=["topic"])
    assert "noise" not in result
    assert result["topic"] == "AI"


def test_extract_empty_goal_returns_nones(extractor: RuleBasedExtractor):
    result = extractor.extract("", slot_names=["a", "b", "c"])
    assert result == {"a": None, "b": None, "c": None}


def test_extract_no_slot_names_returns_empty(extractor: RuleBasedExtractor):
    result = extractor.extract("topic=AI vendors=openai", slot_names=[])
    assert result == {}


def test_extract_value_with_equals_sign_in_quoted(extractor: RuleBasedExtractor):
    goal = 'filter="status=active"'
    result = extractor.extract(goal, slot_names=["filter"])
    assert result["filter"] == "status=active"


def test_extract_integer_like_value_returned_as_string(extractor: RuleBasedExtractor):
    """The stub returns raw strings; type coercion is the caller's job."""
    result = RuleBasedExtractor().extract("count=42", slot_names=["count"])
    assert result["count"] == "42"


def test_extract_duplicate_key_last_wins(extractor: RuleBasedExtractor):
    goal = "topic=first topic=second"
    result = extractor.extract(goal, slot_names=["topic"])
    assert result["topic"] == "second"


def test_extract_case_sensitive_keys(extractor: RuleBasedExtractor):
    goal = "Topic=AI topic=ml"
    result = extractor.extract(goal, slot_names=["topic", "Topic"])
    assert result["topic"] == "ml"
    assert result["Topic"] == "AI"
