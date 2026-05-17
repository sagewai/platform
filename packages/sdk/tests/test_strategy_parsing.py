# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for the shared SLM-tolerant strategy parsers."""

import json

import pytest

from sagewai.core._strategy_utils import parse_json, parse_score


def test_parse_json_plain():
    assert parse_json('["a", "b"]') == ["a", "b"]


def test_parse_json_fenced():
    assert parse_json('```json\n["a", "b"]\n```') == ["a", "b"]


def test_parse_json_bare_fence():
    assert parse_json('```\n{"k": 1}\n```') == {"k": 1}


def test_parse_json_prose_preamble_and_fence():
    raw = 'Here is the JSON you asked for:\n```json\n{"k": 1}\n```'
    assert parse_json(raw) == {"k": 1}


def test_parse_json_prose_preamble_no_fence():
    assert parse_json('Sure! ["x", "y"] is the answer.') == ["x", "y"]


def test_parse_json_garbage_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json("not json at all")


# ── parse_score — characterise the single shared score parser ───────


def test_parse_score_bare_number():
    assert parse_score("7") == 7.0


def test_parse_score_prose():
    assert parse_score("I would rate this a solid 8 out of 10.") == 8.0


def test_parse_score_clamps_high():
    assert parse_score("Score: 99") == 10.0


def test_parse_score_no_number_returns_midpoint():
    assert parse_score("excellent work") == 5.5


def test_tot_and_lats_have_no_private_score_parser():
    """ToT and LATS must delegate to the shared parse_score."""
    import sagewai.core.tree_of_thoughts as tot
    import sagewai.core.lats as lats

    assert not hasattr(tot.TreeOfThoughtsStrategy, "_parse_score")
    assert not hasattr(lats.LATSStrategy, "_parse_score")
