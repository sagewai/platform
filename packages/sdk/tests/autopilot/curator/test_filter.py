"""Tests for the safe quality-filter expression evaluator."""

from __future__ import annotations

import pytest

from sagewai.autopilot.curator.filter import FilterParseError, _eval_filter

# ── Numeric comparisons ────────────────────────────────────────────


def test_gte_passes():
    assert _eval_filter("user_rating >= 4", {"user_rating": 4}) is True
    assert _eval_filter("user_rating >= 4", {"user_rating": 5}) is True


def test_gte_fails():
    assert _eval_filter("user_rating >= 4", {"user_rating": 3}) is False


def test_lte_passes():
    assert _eval_filter("cost <= 0.5", {"cost": 0.5}) is True
    assert _eval_filter("cost <= 0.5", {"cost": 0.3}) is True


def test_lte_fails():
    assert _eval_filter("cost <= 0.5", {"cost": 0.6}) is False


def test_gt_strict():
    assert _eval_filter("score > 0.9", {"score": 0.91}) is True
    assert _eval_filter("score > 0.9", {"score": 0.9}) is False


def test_lt_strict():
    assert _eval_filter("latency < 100", {"latency": 99}) is True
    assert _eval_filter("latency < 100", {"latency": 100}) is False


def test_eq_numeric():
    assert _eval_filter("tier == 3", {"tier": 3}) is True
    assert _eval_filter("tier == 3", {"tier": 4}) is False


def test_ne_numeric():
    assert _eval_filter("tier != 3", {"tier": 4}) is True
    assert _eval_filter("tier != 3", {"tier": 3}) is False


# ── is None / is not None ──────────────────────────────────────────


def test_is_none_passes():
    assert _eval_filter("human_override is None", {"human_override": None}) is True


def test_is_none_fails():
    assert _eval_filter("human_override is None", {"human_override": "correction"}) is False


def test_is_not_none_passes():
    assert _eval_filter("correction is not None", {"correction": "yes"}) is True


def test_is_not_none_fails():
    assert _eval_filter("correction is not None", {"correction": None}) is False


# ── is True / is False ─────────────────────────────────────────────


def test_is_true_passes():
    assert _eval_filter("reviewer_accepted is True", {"reviewer_accepted": True}) is True


def test_is_true_fails():
    assert _eval_filter("reviewer_accepted is True", {"reviewer_accepted": False}) is False


def test_is_false_passes():
    assert _eval_filter("flagged is False", {"flagged": False}) is True


def test_is_false_fails():
    assert _eval_filter("flagged is False", {"flagged": True}) is False


# ── AND / OR boolean chains ────────────────────────────────────────


def test_and_both_pass():
    assert (
        _eval_filter(
            "user_rating >= 4 AND human_override is None",
            {"user_rating": 5, "human_override": None},
        )
        is True
    )


def test_and_first_fails():
    assert (
        _eval_filter(
            "user_rating >= 4 AND human_override is None",
            {"user_rating": 3, "human_override": None},
        )
        is False
    )


def test_and_second_fails():
    assert (
        _eval_filter(
            "user_rating >= 4 AND human_override is None",
            {"user_rating": 4, "human_override": "some text"},
        )
        is False
    )


def test_or_first_passes():
    assert (
        _eval_filter(
            "user_rating >= 4 OR reviewer_accepted is True",
            {"user_rating": 4, "reviewer_accepted": False},
        )
        is True
    )


def test_or_second_passes():
    assert (
        _eval_filter(
            "user_rating >= 4 OR reviewer_accepted is True",
            {"user_rating": 3, "reviewer_accepted": True},
        )
        is True
    )


def test_or_both_fail():
    assert (
        _eval_filter(
            "user_rating >= 4 OR reviewer_accepted is True",
            {"user_rating": 3, "reviewer_accepted": False},
        )
        is False
    )


def test_chained_and():
    assert (
        _eval_filter(
            "a >= 1 AND b >= 2 AND c >= 3",
            {"a": 1, "b": 2, "c": 3},
        )
        is True
    )
    assert (
        _eval_filter(
            "a >= 1 AND b >= 2 AND c >= 3",
            {"a": 1, "b": 2, "c": 2},
        )
        is False
    )


def test_mixed_and_or():
    # AND binds tighter than OR: "a AND b OR c" == "(a AND b) OR c"
    assert (
        _eval_filter(
            "a >= 1 AND b >= 2 OR c >= 3",
            {"a": 0, "b": 0, "c": 3},
        )
        is True
    )
    assert (
        _eval_filter(
            "a >= 1 AND b >= 2 OR c >= 3",
            {"a": 1, "b": 2, "c": 0},
        )
        is True
    )
    assert (
        _eval_filter(
            "a >= 1 AND b >= 2 OR c >= 3",
            {"a": 0, "b": 2, "c": 2},
        )
        is False
    )


# ── Missing keys in context ────────────────────────────────────────


def test_missing_key_returns_false():
    assert _eval_filter("missing_key >= 4", {}) is False


def test_missing_key_in_and_short_circuits():
    assert _eval_filter("missing >= 4 AND user_rating >= 4", {"user_rating": 5}) is False


def test_missing_key_in_or_falls_through():
    assert _eval_filter("missing >= 4 OR user_rating >= 4", {"user_rating": 5}) is True


# ── None expression (no filter) ────────────────────────────────────


def test_none_expr_always_passes():
    from sagewai.autopilot.curator.filter import eval_quality_filter

    assert eval_quality_filter(None, {}) is True
    assert eval_quality_filter(None, {"x": 99}) is True


# ── Whitespace tolerance ───────────────────────────────────────────


def test_extra_whitespace_is_tolerated():
    assert _eval_filter("  user_rating  >=  4  ", {"user_rating": 5}) is True


# ── Malformed expressions raise FilterParseError ───────────────────


def test_empty_string_raises():
    with pytest.raises(FilterParseError):
        _eval_filter("", {})


def test_unknown_operator_raises():
    with pytest.raises(FilterParseError):
        _eval_filter("rating ~~ 4", {"rating": 5})


def test_incomplete_atom_raises():
    with pytest.raises(FilterParseError):
        _eval_filter("user_rating >=", {"user_rating": 4})


def test_bad_is_clause_raises():
    with pytest.raises(FilterParseError):
        _eval_filter("x is whatever", {"x": None})
