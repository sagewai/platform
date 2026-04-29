# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Stateless secret-value redaction for the host-side RPC seam.

Tier-2 plaintext briefly lives in worker memory (per-exec env on
DockerSandboxHandle._exec_env). The Redactor uses that dict as a
list of forbidden substrings and replaces them with a key-named
placeholder before any host-side persistence / log / stream-back.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionConfig:
    """Behaviour knobs. Defaults are deliberately strict; override sparingly."""

    placeholder_template: str = "<redacted:{name}>"
    min_value_length: int = 8
    max_input_bytes: int = 8 * 1024 * 1024


class Redactor:
    """Replace known-secret VALUES (not names) in arbitrary strings.

    Constructed once per sandbox-acquire from the run's resolved
    ``EffectiveProfile``. Values sorted by length descending so longer
    matches win; values below ``min_value_length`` and empty values are
    silently skipped.
    """

    def __init__(
        self,
        secret_values: dict[str, str],
        *,
        config: RedactionConfig | None = None,
    ) -> None:
        self._config = config or RedactionConfig()
        active: list[tuple[str, str]] = [
            (k, v) for k, v in secret_values.items()
            if v and len(v) >= self._config.min_value_length
        ]
        active.sort(key=lambda kv: -len(kv[1]))
        self._values: list[tuple[str, str]] = active
        self.last_skipped_oversize: bool = False

    @property
    def value_count(self) -> int:
        return len(self._values)

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Return ``(redacted_text, matched_key_names)``.

        Empty/None input returns unchanged. Oversize input bypasses
        redaction and sets ``last_skipped_oversize`` (caller emits the
        audit event via ``redact_and_audit``).
        """
        if not text:
            return text, []
        if len(text) > self._config.max_input_bytes:
            self.last_skipped_oversize = True
            return text, []
        self.last_skipped_oversize = False

        result = text
        matched: list[str] = []
        for name, value in self._values:
            if value in result:
                placeholder = self._config.placeholder_template.format(name=name)
                result = result.replace(value, placeholder)
                if name not in matched:
                    matched.append(name)
        return result, matched

    def redact_dict(self, data: Any) -> tuple[Any, list[str]]:
        """Recursively scrub string leaves in dicts / lists / tuples.

        Non-string leaves passed through unchanged. Returns
        ``(redacted_data, sorted_union_of_matches)``.
        """
        matched_set: set[str] = set()

        def _walk(node: Any) -> Any:
            if isinstance(node, str):
                redacted, names = self.redact(node)
                matched_set.update(names)
                return redacted
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_walk(x) for x in node]
            if isinstance(node, tuple):
                return tuple(_walk(x) for x in node)
            return node

        return _walk(data), sorted(matched_set)

    async def redact_and_audit(
        self,
        text: str,
        *,
        surface: str,
        audit_writer: Any,
        run_id: str | None,
        profile_id: str | None,
        tool_name: str | None = None,
    ) -> str:
        """Redact + emit one audit event per matched key.

        Surface examples: ``stdout`` | ``stderr`` | ``error`` |
        ``audit_details`` | ``log_line``. Free-form; included in
        the audit event ``details`` so operators can grep frequencies.
        """
        before_len = len(text or "")
        redacted, matched = self.redact(text)
        after_len = len(redacted or "")

        if self.last_skipped_oversize:
            try:
                await audit_writer.emit(
                    event_type="redaction.skipped_oversize",
                    run_id=run_id,
                    profile_id=profile_id,
                    details={
                        "surface": surface,
                        "byte_count": before_len,
                        "cap": self._config.max_input_bytes,
                    },
                )
            except Exception:
                pass

        for name in matched:
            try:
                await audit_writer.emit(
                    event_type="redaction.match",
                    run_id=run_id,
                    profile_id=profile_id,
                    secret_key=name,
                    actor_type="runtime",
                    details={
                        "surface": surface,
                        "tool": tool_name,
                        "byte_count_before": before_len,
                        "byte_count_after": after_len,
                    },
                )
            except Exception:
                # Audit-emit failure does not block redaction. Sealed-i convention.
                pass

        return redacted


def redact_text(
    text: str,
    *,
    secret_values: dict[str, str],
    config: RedactionConfig | None = None,
) -> tuple[str, list[str]]:
    """Free-function shortcut for callers without a long-lived Redactor."""
    return Redactor(secret_values, config=config).redact(text)
