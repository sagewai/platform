# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Self-correction loop — error recovery with failure exemplars.

Detects schema violations and malformed tool outputs, then re-prompts the LLM
with a 1-shot correction example (PALADIN-style) to recover gracefully.

Usage::

    from sagewai.core.self_correction import SelfCorrectionStrategy

    agent = UniversalAgent(
        name="Auditor",
        model="gpt-4o",
        strategy=SelfCorrectionStrategy(max_corrections=2),
    )

The strategy wraps around a base strategy (default: ReActStrategy) and
intercepts errors to attempt correction before giving up.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sagewai.core.events import AgentEvent
from sagewai.core.strategies import ReActStrategy
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.core.strategies import ExecutionStrategy

logger = logging.getLogger(__name__)


class CorrectionError(Exception):
    """Raised when self-correction exhausts all attempts."""

    def __init__(self, original_error: str, attempts: int) -> None:
        self.original_error = original_error
        self.attempts = attempts
        super().__init__(f"Self-correction failed after {attempts} attempts: {original_error}")


class FailureExemplar:
    """A stored example of a failure and its correction.

    Used to provide 1-shot correction context to the LLM when similar
    errors are encountered.
    """

    def __init__(
        self,
        *,
        error_type: str,
        bad_output: str,
        correction_prompt: str,
        corrected_output: str,
    ) -> None:
        self.error_type = error_type
        self.bad_output = bad_output
        self.correction_prompt = correction_prompt
        self.corrected_output = corrected_output


class ExemplarStore:
    """In-memory store for failure exemplars, keyed by error type."""

    def __init__(self) -> None:
        self._exemplars: dict[str, list[FailureExemplar]] = {}

    def add(self, exemplar: FailureExemplar) -> None:
        """Store a failure exemplar."""
        self._exemplars.setdefault(exemplar.error_type, []).append(exemplar)

    def find(self, error_type: str) -> FailureExemplar | None:
        """Find the most recent exemplar matching the error type."""
        exemplars = self._exemplars.get(error_type, [])
        return exemplars[-1] if exemplars else None

    def find_best(self, error_message: str) -> FailureExemplar | None:
        """Find the best matching exemplar by scanning all error types.

        Uses substring matching — returns the first exemplar whose error_type
        appears in the error message (case-insensitive).
        """
        error_lower = error_message.lower()
        for error_type, exemplars in self._exemplars.items():
            if error_type.lower() in error_lower:
                return exemplars[-1]
        return None

    def clear(self) -> None:
        """Remove all stored exemplars."""
        self._exemplars.clear()

    @property
    def count(self) -> int:
        """Total number of stored exemplars."""
        return sum(len(v) for v in self._exemplars.values())


def _strip_markdown_json(text: str) -> str:
    """Strip markdown code fences from JSON output.

    LLMs commonly wrap JSON in ```json ... ``` blocks.
    """
    import re

    if not text:
        return text or ""
    stripped = text.strip()
    # Match ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()
    return stripped


def validate_json_output(text: str) -> dict[str, Any]:
    """Validate that text is parseable JSON. Raises ValueError if not."""
    cleaned = _strip_markdown_json(text)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Invalid JSON output: {exc}") from exc


def validate_schema(data: dict[str, Any], required_fields: list[str]) -> None:
    """Validate that a dict contains all required fields. Raises ValueError."""
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


class OutputValidator:
    """Validates LLM outputs against expected schemas.

    Register validators to check assistant responses before accepting them.
    """

    def __init__(self) -> None:
        self._validators: list[tuple[str, Any]] = []

    def add_json_validator(self, required_fields: list[str] | None = None) -> None:
        """Add a validator that checks for valid JSON with optional required fields."""
        self._validators.append(("json", required_fields or []))

    def add_custom_validator(self, name: str, fn: Any) -> None:
        """Add a custom validator function ``(str) -> None`` that raises on failure."""
        self._validators.append(("custom", (name, fn)))

    def validate(self, text: str) -> list[str]:
        """Run all validators. Returns list of error messages (empty = valid)."""
        errors: list[str] = []
        for vtype, config in self._validators:
            try:
                if vtype == "json":
                    data = validate_json_output(text)
                    if config:
                        validate_schema(data, config)
                elif vtype == "custom":
                    name, fn = config
                    fn(text)
            except (ValueError, TypeError) as exc:
                errors.append(str(exc))
        return errors

    @property
    def has_validators(self) -> bool:
        return len(self._validators) > 0


class SelfCorrectionStrategy:
    """Execution strategy with automatic error recovery.

    Wraps a base strategy and intercepts validation errors to re-prompt
    the LLM with correction context, optionally including a failure exemplar.

    Parameters
    ----------
    base_strategy:
        The inner strategy to delegate to (default: ReActStrategy).
    max_corrections:
        Maximum correction attempts per run before raising CorrectionError.
    validator:
        Optional OutputValidator to check assistant responses.
    exemplar_store:
        Optional ExemplarStore for 1-shot correction examples.
    """

    def __init__(
        self,
        *,
        base_strategy: ExecutionStrategy | None = None,
        max_corrections: int = 2,
        validator: OutputValidator | None = None,
        exemplar_store: ExemplarStore | None = None,
    ) -> None:
        self.base_strategy = base_strategy or ReActStrategy()
        self.max_corrections = max(1, max_corrections)
        self.validator = validator
        self.exemplar_store = exemplar_store or ExemplarStore()

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Execute with self-correction on validation failures."""
        last_error = ""

        for attempt in range(self.max_corrections + 1):
            result = await self.base_strategy.execute(agent, messages, tools, max_iterations)

            # If no validator configured, return immediately
            if not self.validator or not self.validator.has_validators:
                return result

            # Validate the output
            text = result.content or ""
            errors = self.validator.validate(text)

            if not errors:
                if attempt > 0:
                    await agent._emit(
                        AgentEvent.STEP_FINISHED,
                        {"step": "self_correction", "attempt": attempt, "status": "recovered"},
                    )
                return result

            last_error = "; ".join(errors)
            logger.warning(
                "Agent %s output validation failed (attempt %d/%d): %s",
                agent.config.name,
                attempt + 1,
                self.max_corrections + 1,
                last_error,
            )

            if attempt >= self.max_corrections:
                break

            # Build correction prompt
            correction = self._build_correction_prompt(text, last_error)
            messages.append(result)
            messages.append(ChatMessage.user(correction))

            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": "self_correction", "attempt": attempt + 1, "error": last_error},
            )

        raise CorrectionError(last_error, self.max_corrections)

    def _build_correction_prompt(self, bad_output: str, error: str) -> str:
        """Build a correction prompt, optionally including an exemplar."""
        parts = [
            "Your previous output had the following error:",
            f"Error: {error}",
            "",
            f"Your output was: {bad_output[:500]}",
            "",
        ]

        # Try to find a relevant exemplar
        exemplar = self.exemplar_store.find_best(error)
        if exemplar:
            parts.extend(
                [
                    "Here is an example of a similar error and its correction:",
                    f"Bad output: {exemplar.bad_output}",
                    f"Corrected output: {exemplar.corrected_output}",
                    "",
                ]
            )

        parts.append("Please fix the error and provide corrected output.")
        return "\n".join(parts)
