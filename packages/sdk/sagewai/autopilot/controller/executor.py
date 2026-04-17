# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AgentExecutor — runs a single agent node against a mission context.

This module bridges the autopilot graph-walk loop with real LLM
inference via LiteLLM. For deterministic nodes it short-circuits
immediately; for LLM nodes it calls ``litellm.acompletion`` and
gracefully degrades to a "skipped" result when no API key is configured,
or to a "failed" result when the call raises an unexpected error.
"""

from __future__ import annotations

import logging
import pathlib

from pydantic import BaseModel, ConfigDict

from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent

from .types import StepResult

logger = logging.getLogger(__name__)

_NO_PROVIDER_SENTINEL = "No LLM provider configured"

_GENERIC_SYSTEM_PROMPT = (
    "You are an AI agent operating within the Sagewai autopilot framework. "
    "Complete the task described in the user message as thoroughly as possible."
)


class ExecutorConfig(BaseModel):
    """Injectable configuration for :class:`AgentExecutor`."""

    model_config = ConfigDict(frozen=True)

    model: str = "gpt-4o-mini"
    max_tokens: int = 2048
    temperature: float = 0.3


class AgentExecutor:
    """Executes a single agent node and returns a :class:`StepResult`.

    Calling :meth:`execute` is the only public entry point.  The method
    never raises — all errors are caught and reflected in the returned
    ``StepResult.status``.

    Args:
        config: Optional :class:`ExecutorConfig`.  Defaults are used if
            not provided.
    """

    def __init__(self, config: ExecutorConfig | None = None) -> None:
        self._config = config or ExecutorConfig()

    async def execute(self, agent: Agent, context: dict) -> StepResult:
        """Execute *agent* given the current *context* dict.

        Args:
            agent: The :class:`~sagewai.autopilot.agent_graph.Agent` node
                to execute.
            context: Accumulating context dict.  For LLM nodes this is
                serialised into the user message.

        Returns:
            A :class:`StepResult` with ``status`` of ``"completed"``,
            ``"skipped"``, or ``"failed"``.
        """
        if agent.kind is AgentKind.DETERMINISTIC:
            return StepResult(
                node_id=agent.id,
                status="completed",
                output_preview="deterministic pass-through",
            )

        # LLM path
        return await self._run_llm(agent, context)

    # ── private ─────────────────────────────────────────────────────

    async def _run_llm(self, agent: Agent, context: dict) -> StepResult:
        """Call litellm and return a StepResult.  Never raises."""
        try:
            import litellm  # local import — optional dependency
        except ModuleNotFoundError:
            logger.warning("litellm not installed — skipping LLM agent %r", agent.id)
            return StepResult(
                node_id=agent.id,
                status="skipped",
                output_preview=_NO_PROVIDER_SENTINEL,
            )

        system_prompt = self._load_prompt(agent.prompt_ref)
        user_message = _build_user_message(context)

        try:
            response = await litellm.acompletion(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )
            text: str = response.choices[0].message.content or ""
            preview = text[:200]
            logger.debug("Agent %r completed (preview=%r)", agent.id, preview[:60])
            return StepResult(
                node_id=agent.id,
                status="completed",
                output_preview=preview,
            )
        except litellm.exceptions.AuthenticationError:
            logger.warning("No API key for agent %r — skipping (AuthenticationError)", agent.id)
            return StepResult(
                node_id=agent.id,
                status="skipped",
                output_preview=_NO_PROVIDER_SENTINEL,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent %r LLM call failed: %s", agent.id, exc)
            return StepResult(
                node_id=agent.id,
                status="failed",
                output_preview=str(exc)[:200],
            )

    @staticmethod
    def _load_prompt(prompt_ref: str | None) -> str:
        """Load prompt text from *prompt_ref* file path, or return generic."""
        if prompt_ref is None:
            return _GENERIC_SYSTEM_PROMPT
        path = pathlib.Path(prompt_ref)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        logger.debug("prompt_ref %r not found — using generic prompt", prompt_ref)
        return _GENERIC_SYSTEM_PROMPT


def _build_user_message(context: dict) -> str:
    """Serialise the context dict into a human-readable user message."""
    if not context:
        return "No additional context provided. Proceed with the task."
    lines = ["Current mission context:"]
    for key, value in context.items():
        if key.startswith("__"):
            continue  # skip internal slots like __blueprint_json__
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)
