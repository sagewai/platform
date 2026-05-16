# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CapabilityGapSource — exception-driven credential-gap signal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sagewai.core.state import StepStatus
from sagewai.sealed.directives.models import SignalEvent
from sagewai.sealed.directives.signals import SignalContext

_CREDENTIAL_ERROR_TYPES = (
    "MissingKeyError",
    "AuthenticationError",
    "SecretRevokedError",
)

# Match "MissingKeyError: KEY_NAME ..." or "AuthenticationError: KEY_NAME ..."
_ERROR_TYPE_RE = re.compile(r"^(\w+Error)\s*:")
_KEY_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


def _classify_error(error: str | None) -> str | None:
    if not error:
        return None
    m = _ERROR_TYPE_RE.match(error)
    if not m:
        return None
    return m.group(1)


def _extract_missing_key(error: str | None) -> str | None:
    if not error:
        return None
    m = _KEY_NAME_RE.search(error)
    if not m:
        return None
    candidate = m.group(1)
    # Skip the matched error class name itself if it's all-caps suffixed.
    if candidate.endswith("ERROR"):
        for cand in _KEY_NAME_RE.findall(error)[1:]:
            return cand
        return None
    return candidate


class _StoreView(Protocol):
    def step_name_at_index(self, run, idx: int) -> str | None: ...
    def suggest_profile_for_key(self, key: str) -> str | None: ...


@dataclass
class CapabilityGapSource:
    """Signal source — last step failed with credential-related error."""

    name: str = "capability_gap"

    async def collect(
        self,
        *,
        run,
        step_index: int,
        context: SignalContext,
    ) -> list[SignalEvent]:
        if step_index == 0:
            return []
        store: _StoreView | None = context.store
        if store is None:
            return []
        prev_name = store.step_name_at_index(run, step_index - 1)
        if prev_name is None:
            return []
        prev = run.steps.get(prev_name)
        if prev is None or prev.status != StepStatus.FAILED:
            return []
        error_type = _classify_error(prev.error)
        if error_type not in _CREDENTIAL_ERROR_TYPES:
            return []
        missing_key = _extract_missing_key(prev.error)
        if not missing_key:
            return []
        suggested = store.suggest_profile_for_key(missing_key)
        return [
            SignalEvent(
                kind="capability_gap",
                run_id=run.run_id,
                project_id=getattr(run, "project_id", None),
                workflow_name=run.workflow_name,
                step_index=step_index,
                severity="warning",
                detail=f"Step {step_index - 1} ({prev_name}): missing {missing_key}",
                evidence={
                    "missing_key": missing_key,
                    "suggested_profile": suggested,
                    "error_type": error_type,
                    "step_index": step_index - 1,
                    "step_name": prev_name,
                },
                emitted_at=datetime.now(tz=timezone.utc),
            )
        ]
