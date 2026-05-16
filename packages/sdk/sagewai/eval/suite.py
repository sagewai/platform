# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Eval suite — orchestrate running all cases and aggregating results."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sagewai.core.base import BaseAgent
from sagewai.eval.dataset import EvalDataset
from sagewai.eval.judge import EvalScore, LLMJudge

logger = logging.getLogger(__name__)


@dataclass
class EvalResults:
    """Aggregated results from an eval suite run."""

    scores: list[EvalScore] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        total = len(self.scores)
        passed = sum(1 for s in self.scores if s.passed)
        avg_score = sum(s.score for s in self.scores) / total if total else 0.0
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total else 0.0,
            "avg_score": avg_score,
        }

    def to_jsonl(self, path: str) -> None:
        with open(path, "w") as f:
            for score in self.scores:
                f.write(json.dumps(score.to_dict()) + "\n")


class EvalSuite:
    """Run all eval cases against an agent and score with a judge."""

    def __init__(
        self,
        *,
        agent: BaseAgent,
        dataset: EvalDataset,
        judge: LLMJudge,
    ) -> None:
        self.agent = agent
        self.dataset = dataset
        self.judge = judge

    async def run(self) -> EvalResults:
        """Execute all cases and return scored results."""
        scores: list[EvalScore] = []
        for case in self.dataset.cases:
            try:
                output = await self.agent.chat(case.input)
                score = await self.judge.score(case, output)
                scores.append(score)
            except Exception as exc:
                logger.error("Eval case failed: %s — %s", case.input[:50], exc)
                scores.append(EvalScore(
                    passed=False,
                    score=0.0,
                    reasoning=f"Agent error: {exc}",
                ))
        return EvalResults(scores=scores)
