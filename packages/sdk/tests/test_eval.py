# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the evaluation framework."""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.eval.dataset import EvalCase, EvalDataset
from sagewai.eval.judge import EvalScore, LLMJudge
from sagewai.eval.suite import EvalResults, EvalSuite
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class FixedAgent(BaseAgent):
    def __init__(self, response: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._response = response

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        return ChatMessage.assistant(self._response)


# ---------------------------------------------------------------------------
# EvalCase & EvalDataset
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_create(self):
        case = EvalCase(
            input="What is Python?",
            expected_output="A programming language",
            agent_name="test",
            criteria=["factually_accurate"],
        )
        assert case.input == "What is Python?"
        assert case.criteria == ["factually_accurate"]

    def test_optional_fields(self):
        case = EvalCase(input="hi", agent_name="test", criteria=["relevant"])
        assert case.expected_output is None
        assert case.metadata == {}


class TestEvalDataset:
    def test_from_cases(self):
        cases = [
            EvalCase(input="q1", agent_name="a", criteria=["c1"]),
            EvalCase(input="q2", agent_name="a", criteria=["c1"]),
        ]
        ds = EvalDataset(cases=cases)
        assert len(ds) == 2

    def test_from_jsonl(self):
        cases = [
            {"input": "q1", "agent_name": "a", "criteria": ["c1"]},
            {"input": "q2", "agent_name": "a", "criteria": ["c1"], "expected_output": "ans"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for case in cases:
                f.write(json.dumps(case) + "\n")
            f.flush()
            ds = EvalDataset.from_jsonl(f.name)
            assert len(ds) == 2
            assert ds.cases[1].expected_output == "ans"

    def test_to_jsonl(self):
        cases = [EvalCase(input="q1", agent_name="a", criteria=["c1"])]
        ds = EvalDataset(cases=cases)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            ds.to_jsonl(f.name)
        loaded = EvalDataset.from_jsonl(f.name)
        assert len(loaded) == 1


# ---------------------------------------------------------------------------
# EvalScore & EvalResults
# ---------------------------------------------------------------------------


class TestEvalScore:
    def test_passed(self):
        score = EvalScore(passed=True, score=9.0, reasoning="Good")
        assert score.passed
        assert score.score == 9.0

    def test_failed(self):
        score = EvalScore(passed=False, score=3.0, reasoning="Inaccurate")
        assert not score.passed


class TestEvalResults:
    def test_summary(self):
        results = EvalResults(
            scores=[
                EvalScore(passed=True, score=9.0, reasoning="ok"),
                EvalScore(passed=True, score=8.0, reasoning="ok"),
                EvalScore(passed=False, score=3.0, reasoning="bad"),
            ]
        )
        summary = results.summary()
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["pass_rate"] == pytest.approx(2 / 3, rel=1e-2)
        assert summary["avg_score"] == pytest.approx(20 / 3, rel=1e-2)

    def test_to_jsonl(self):
        results = EvalResults(
            scores=[EvalScore(passed=True, score=9.0, reasoning="ok")]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            results.to_jsonl(f.name)
        with open(f.name) as fh:
            lines = fh.readlines()
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# EvalSuite (with mock judge)
# ---------------------------------------------------------------------------


class MockJudge(LLMJudge):
    """Judge that returns a fixed score without calling an LLM."""

    def __init__(self, score: float = 8.0, passed: bool = True):
        super().__init__(model="mock")
        self._score = score
        self._passed = passed

    async def score(self, case: EvalCase, actual_output: str) -> EvalScore:
        return EvalScore(passed=self._passed, score=self._score, reasoning="mock")


class TestEvalSuite:
    @pytest.mark.asyncio
    async def test_run_all_cases(self):
        agent = FixedAgent(response="Python is a language", name="test")
        dataset = EvalDataset(cases=[
            EvalCase(input="What is Python?", agent_name="test", criteria=["accurate"]),
            EvalCase(input="What is Java?", agent_name="test", criteria=["accurate"]),
        ])
        judge = MockJudge(score=9.0, passed=True)
        suite = EvalSuite(agent=agent, dataset=dataset, judge=judge)
        results = await suite.run()
        assert len(results.scores) == 2
        assert results.summary()["pass_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_mixed_results(self):
        agent = FixedAgent(response="I don't know", name="test")
        dataset = EvalDataset(cases=[
            EvalCase(input="q1", agent_name="test", criteria=["accurate"]),
        ])
        judge = MockJudge(score=2.0, passed=False)
        suite = EvalSuite(agent=agent, dataset=dataset, judge=judge)
        results = await suite.run()
        assert results.summary()["passed"] == 0
