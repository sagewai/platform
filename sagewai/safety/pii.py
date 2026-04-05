# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PII (Personally Identifiable Information) detection and redaction guardrail.

Optional per-agent/per-workflow guardrail that detects and handles PII
before it reaches the LLM (input) or the user (output).

Usage:
    from sagewai.safety.pii import PIIGuard, PIIEntityType

    agent = UniversalAgent(
        name="safe-agent",
        model="gpt-4o",
        guardrails=[PIIGuard(action="redact", entity_types=[PIIEntityType.EMAIL])],
    )
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from sagewai.safety.guardrails import Guardrail, GuardrailResult


class PIIEntityType(str, Enum):
    """Types of PII entities that can be detected."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    IBAN = "IBAN"
    IP_ADDRESS = "IP_ADDRESS"
    PASSPORT = "PASSPORT"


# Regex patterns for each PII entity type
_PII_PATTERNS: dict[PIIEntityType, re.Pattern[str]] = {
    PIIEntityType.EMAIL: re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    ),
    PIIEntityType.PHONE: re.compile(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    ),
    PIIEntityType.SSN: re.compile(r"\b\d{3}-\d{2}-\d{3,4}\b"),
    PIIEntityType.CREDIT_CARD: re.compile(
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
    ),
    PIIEntityType.IBAN: re.compile(
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]?){0,16}\b"
    ),
    PIIEntityType.IP_ADDRESS: re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ),
    PIIEntityType.PASSPORT: re.compile(
        r"\b[A-Z]{1,2}\d{6,9}\b"
    ),
}

# Redaction labels per entity type
_REDACTION_LABELS: dict[PIIEntityType, str] = {
    PIIEntityType.EMAIL: "[REDACTED_EMAIL]",
    PIIEntityType.PHONE: "[REDACTED_PHONE]",
    PIIEntityType.SSN: "[REDACTED_SSN]",
    PIIEntityType.CREDIT_CARD: "[REDACTED_CARD]",
    PIIEntityType.IBAN: "[REDACTED_IBAN]",
    PIIEntityType.IP_ADDRESS: "[REDACTED_IP]",
    PIIEntityType.PASSPORT: "[REDACTED_PASSPORT]",
}


class PIIGuard(Guardrail):
    """Detect and handle PII in agent inputs and outputs.

    Actions:
        block: Reject the message entirely (raises GuardrailViolationError)
        redact: Replace PII with [REDACTED_TYPE] labels
        warn: Log violation but allow message through
        escalate: Emit escalation event, allow message
        log_only: Detect and log but treat as warning

    Args:
        action: How to handle detected PII.
        entity_types: Which PII types to detect. None = all types.
    """

    def __init__(
        self,
        *,
        action: Literal["block", "redact", "warn", "escalate", "log_only"] = "block",
        entity_types: list[PIIEntityType] | None = None,
    ) -> None:
        self.action = action
        self.entity_types = entity_types or list(PIIEntityType)
        self._patterns = {
            et: _PII_PATTERNS[et] for et in self.entity_types if et in _PII_PATTERNS
        }

    def detect(self, text: str) -> list[tuple[PIIEntityType, str]]:
        """Detect all PII entities in text.

        Returns list of (entity_type, matched_text) tuples.
        """
        findings: list[tuple[PIIEntityType, str]] = []
        for entity_type, pattern in self._patterns.items():
            for match in pattern.finditer(text):
                findings.append((entity_type, match.group()))
        return findings

    def redact(self, text: str) -> str:
        """Replace all detected PII with redaction labels."""
        result = text
        for entity_type, pattern in self._patterns.items():
            label = _REDACTION_LABELS.get(entity_type, "[REDACTED]")
            result = pattern.sub(label, result)
        return result

    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        """Check input message for PII."""
        return self._check(message)

    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        """Check output response for PII."""
        return self._check(response)

    def _check(self, text: str) -> GuardrailResult:
        """Internal check — detect PII and return result based on action."""
        findings = self.detect(text)
        if not findings:
            return GuardrailResult(passed=True)

        entity_types_found = sorted(set(et.value for et, _ in findings))
        violation = f"PII detected: {', '.join(entity_types_found)} ({len(findings)} instances)"

        # Map our actions to guardrail-compatible actions
        guardrail_action: Literal["block", "warn", "escalate"]
        if self.action in ("block", "redact"):
            guardrail_action = "block"
        elif self.action in ("warn", "log_only"):
            guardrail_action = "warn"
        else:
            guardrail_action = "escalate"

        return GuardrailResult(
            passed=False,
            violation=violation,
            action=guardrail_action,
        )
