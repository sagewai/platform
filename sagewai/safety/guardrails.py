# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Guardrail framework — pre/post validation for agent inputs and outputs."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""

    passed: bool
    violation: str | None = None
    action: Literal["block", "warn", "escalate"] = "block"


class GuardrailViolationError(Exception):
    """Raised when a guardrail blocks an input or output."""

    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        super().__init__(result.violation or "Guardrail violation")


class Guardrail(ABC):
    """Base class for guardrails."""

    @abstractmethod
    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        """Validate an input message before it reaches the LLM."""
        ...

    @abstractmethod
    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        """Validate an output response before it reaches the user."""
        ...


class ContentFilter(Guardrail):
    """Block messages containing forbidden words or regex patterns."""

    def __init__(
        self,
        *,
        blocklist: list[str] | None = None,
        patterns: list[str] | None = None,
        action: Literal["block", "warn", "escalate"] = "block",
    ) -> None:
        self.blocklist = [w.lower() for w in (blocklist or [])]
        self.patterns = [re.compile(p, re.IGNORECASE) for p in (patterns or [])]
        self.action = action

    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        return self._check(message)

    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        return self._check(response)

    def _check(self, text: str) -> GuardrailResult:
        lower = text.lower()
        for word in self.blocklist:
            if word in lower:
                return GuardrailResult(
                    passed=False,
                    violation=f"Blocked content: '{word}' found in text",
                    action=self.action,
                )
        for pattern in self.patterns:
            if pattern.search(text):
                return GuardrailResult(
                    passed=False,
                    violation=f"Blocked pattern matched: {pattern.pattern}",
                    action=self.action,
                )
        return GuardrailResult(passed=True)


class OutputSchemaGuard(Guardrail):
    """Validate that output conforms to a JSON schema."""

    def __init__(self, *, schema: dict[str, Any]) -> None:
        self.schema = schema

    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        return GuardrailResult(passed=True)

    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return GuardrailResult(
                passed=False,
                violation="Output is not valid JSON",
            )
        # Check required fields
        required = self.schema.get("required", [])
        if isinstance(data, dict):
            missing = [f for f in required if f not in data]
            if missing:
                return GuardrailResult(
                    passed=False,
                    violation=f"Missing required fields: {missing}",
                )
        return GuardrailResult(passed=True)


class TokenBudgetGuard(Guardrail):
    """Block requests that would exceed a cost budget."""

    def __init__(self, *, max_usd: float) -> None:
        self.max_usd = max_usd

    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        cost_so_far = context.get("cost_usd_so_far", 0.0)
        if cost_so_far > self.max_usd:
            return GuardrailResult(
                passed=False,
                violation=f"Budget exceeded: ${cost_so_far:.4f} > ${self.max_usd:.4f}",
            )
        return GuardrailResult(passed=True)

    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        return GuardrailResult(passed=True)
