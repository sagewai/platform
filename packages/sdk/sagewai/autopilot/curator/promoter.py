# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Promoter — A/B evaluation gate for fine-tuned candidate models.

The :class:`Promoter` is stateless and reusable. It parses the
``promotion_criteria`` string from a :class:`LearningLoopConfig` using
the same safe expression evaluator as the :class:`Curator` and produces
a frozen :class:`PromotionResult`.

Typical usage::

    promoter = Promoter.from_blueprint(blueprint)
    result = promoter.promote(
        candidate_model_id="llama-3.1-8b-finetuned-v2",
        metrics={"accuracy": 0.94, "cost": 0.45},
        criteria=blueprint.learning_loop_target.promotion_criteria,
    )
    if result.promoted:
        register_provider(result.candidate_model_id)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .filter import FilterParseError, _eval_filter
from .types import PromotionResult

if TYPE_CHECKING:
    from sagewai.autopilot.blueprint import Blueprint


class Promoter:
    """Stateless A/B evaluation gate for fine-tuned candidate models.

    The :meth:`promote` method evaluates ``criteria`` against the supplied
    ``metrics`` dict using :func:`_eval_filter`.  No LLM calls, no network
    calls — fully deterministic and synchronous.
    """

    def promote(
        self,
        candidate_model_id: str,
        metrics: dict[str, float],
        criteria: str,
    ) -> PromotionResult:
        """Evaluate a candidate model against promotion criteria.

        Args:
            candidate_model_id: Identifier of the fine-tuned model under
                evaluation (e.g. an Ollama tag or HuggingFace model ID).
            metrics: Observed metric values from the eval run.  Keys must
                match the identifiers in ``criteria`` (e.g.
                ``{"accuracy": 0.94, "cost": 0.45}``).
            criteria: Promotion criteria string from
                :attr:`LearningLoopConfig.promotion_criteria`.

        Returns:
            A frozen :class:`PromotionResult`.  ``promoted=True`` iff
            *every* criterion in the expression is satisfied.
        """
        context: dict[str, Any] = dict(metrics)
        try:
            passed = _eval_filter(criteria, context)
        except FilterParseError as exc:
            return PromotionResult(
                promoted=False,
                reason=f"Criteria parse error: {exc}",
                metrics=metrics,
                candidate_model_id=candidate_model_id,
            )

        if passed:
            metric_summary = ", ".join(f"{k}={v}" for k, v in metrics.items())
            reason = (
                f"All criteria met [{criteria}]: {metric_summary}"
                if metric_summary
                else f"All criteria met [{criteria}]"
            )
        else:
            metric_summary = ", ".join(f"{k}={v}" for k, v in metrics.items())
            reason = (
                f"Criteria not met [{criteria}]: observed {metric_summary}"
                if metric_summary
                else f"Criteria not met [{criteria}]: no metrics provided"
            )

        return PromotionResult(
            promoted=passed,
            reason=reason,
            metrics=metrics,
            candidate_model_id=candidate_model_id,
        )

    @classmethod
    def from_blueprint(cls, blueprint: Blueprint) -> Promoter:
        """Construct a :class:`Promoter` validated against a blueprint.

        This is a no-op factory (the Promoter is stateless) but it
        validates that the blueprint actually has a ``learning_loop_target``
        before the caller tries to use it.

        Args:
            blueprint: The blueprint whose :attr:`~Blueprint.learning_loop_target`
                will supply the promotion criteria.

        Returns:
            A fresh :class:`Promoter` instance.

        Raises:
            ValueError: If ``blueprint.learning_loop_target`` is ``None``.
        """
        if blueprint.learning_loop_target is None:
            raise ValueError(
                f"Blueprint {blueprint.id!r} has no learning_loop_target — "
                "cannot construct a Promoter from it."
            )
        return cls()
