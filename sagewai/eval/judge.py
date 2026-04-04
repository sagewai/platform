# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM-as-judge — score agent outputs against criteria."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sagewai.eval.dataset import EvalCase

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are an impartial evaluator. Score the agent's response on a scale of 1-10.

Criteria to evaluate: {criteria}

{expected_section}

Agent's input: {input}
Agent's output: {output}

Respond with JSON: {{"score": <1-10>, "passed": <true/false>, "reasoning": "<brief explanation>"}}
Score >= 7 means passed. Be strict but fair."""


@dataclass
class EvalScore:
    """Result of evaluating one case."""

    passed: bool
    score: float
    reasoning: str
    criteria_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "reasoning": self.reasoning,
            "criteria_scores": self.criteria_scores,
        }


class LLMJudge:
    """Scores agent output using a separate LLM call."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model

    async def score(self, case: EvalCase, actual_output: str) -> EvalScore:
        """Score the actual output against the eval case."""
        import litellm

        expected_section = ""
        if case.expected_output:
            expected_section = f"Expected output: {case.expected_output}"

        prompt = _JUDGE_PROMPT.format(
            criteria=", ".join(case.criteria),
            expected_section=expected_section,
            input=case.input,
            output=actual_output,
        )

        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        text = response.choices[0].message.content.strip()
        try:
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            data = json.loads(text)
            return EvalScore(
                passed=data.get("passed", data.get("score", 0) >= 7),
                score=float(data.get("score", 0)),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to parse judge response: %s", text[:200])
            return EvalScore(passed=False, score=0.0, reasoning=f"Parse error: {exc}")
