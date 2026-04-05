# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Evaluation framework — golden datasets, LLM-as-judge scoring, eval suites."""

from sagewai.eval.dataset import EvalCase, EvalDataset
from sagewai.eval.judge import EvalScore, LLMJudge
from sagewai.eval.suite import EvalResults, EvalSuite

__all__ = [
    "EvalCase",
    "EvalDataset",
    "EvalResults",
    "EvalScore",
    "EvalSuite",
    "LLMJudge",
]
