# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GoalRouter — the top-level orchestrator for Plan 3.

:class:`GoalRouter` ties together:

1. **Retrieval** — calls :meth:`SagewaiLLMClient.retrieve_blueprints` to
   fetch scored candidates from the hosted service.
2. **Confidence gating** — delegates to :func:`gate` with the injected
   :class:`ConfidenceConfig` to decide the outcome band.
3. **Slot extraction** — for AUTO_ROUTE outcomes, calls the injected
   :class:`SlotExtractor` to parse slot values from the goal string.
4. **Preview building** — assembles a human-readable plan card via
   :func:`build_preview` so the caller can present it for user approval.

All network errors and service errors are caught internally and produce a
:class:`SynthesisNeeded` fallback, consistent with the graceful-
degradation contract from Plan 2.
"""

from __future__ import annotations

import logging

from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.sagewai_llm import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.errors import ClientError

from .confidence import ConfidenceConfig, RoutingDecision, gate
from .extractor import RuleBasedExtractor, SlotExtractor
from .preview import build_preview
from .types import AutoRouted, PickerNeeded, RankedBlueprint, RoutingResult, SynthesisNeeded

logger = logging.getLogger(__name__)


class GoalRouter:
    """Async router that maps a plain-English goal to a :class:`RoutingResult`.

    Args:
        client: A connected :class:`SagewaiLLMClient` (Plan 2).
        config: Confidence threshold configuration.  Defaults to the
            standard thresholds (0.85 / 0.65) if not supplied.
        extractor: Slot extractor implementation.  Defaults to the
            :class:`RuleBasedExtractor` stub.  Plan 4 will inject an
            LLM-backed extractor here.
        retrieve_k: How many candidates to request from the service.
            Defaults to 5.
    """

    def __init__(
        self,
        *,
        client: SagewaiLLMClient,
        config: ConfidenceConfig | None = None,
        extractor: SlotExtractor | None = None,
        retrieve_k: int = 5,
    ) -> None:
        self._client = client
        self._config = config or ConfidenceConfig()
        self._extractor: SlotExtractor = extractor or RuleBasedExtractor()
        self._retrieve_k = retrieve_k

    async def route(self, goal: str) -> RoutingResult:
        """Route *goal* to the appropriate outcome.

        Args:
            goal: Plain-English description of what the user wants to
                accomplish.

        Returns:
            One of :class:`AutoRouted`, :class:`PickerNeeded`, or
            :class:`SynthesisNeeded`.  Network failures and service
            errors degrade gracefully to :class:`SynthesisNeeded`.
        """
        candidates = await self._retrieve_candidates(goal)
        decision = gate(candidates, self._config)

        if decision == RoutingDecision.AUTO_ROUTE:
            return self._build_auto_routed(goal, candidates)

        if decision == RoutingDecision.PICKER:
            top_k = candidates[: self._config.picker_top_k]
            return PickerNeeded(candidates=tuple(top_k))

        # SYNTHESIZE — also the fallback for any retrieval failure.
        return SynthesisNeeded(goal=goal)

    # ── private helpers ────────────────────────────────────────────

    async def _retrieve_candidates(self, goal: str) -> tuple[RankedBlueprint, ...]:
        """Call the hosted service and return scored candidates.

        Returns an empty tuple on any error (network, quota, service) so
        that :func:`gate` gracefully falls through to SYNTHESIZE.
        """
        try:
            response = await self._client.retrieve_blueprints(goal=goal, k=self._retrieve_k)
            return tuple(
                RankedBlueprint(
                    blueprint_json=c.blueprint_json,
                    score=c.score,
                )
                for c in response.candidates
            )
        except ClientError as exc:
            logger.warning("Sagewai LLM client error during retrieval: %s", exc)
            return ()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error during blueprint retrieval: %s", exc)
            return ()

    def _build_auto_routed(self, goal: str, candidates: tuple[RankedBlueprint, ...]) -> AutoRouted:
        """Build an :class:`AutoRouted` result from the top candidate."""
        top = candidates[0]
        blueprint = Blueprint.model_validate_json(top.blueprint_json)
        slot_names = list(blueprint.required_slots) + list(blueprint.optional_slots)
        slots = self._extractor.extract(goal, slot_names=slot_names)
        preview = build_preview(blueprint, slots=slots)
        return AutoRouted(ranked=top, slots=slots, preview=preview)
