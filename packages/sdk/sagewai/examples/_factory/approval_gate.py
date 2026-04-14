# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Human-in-the-loop approval gate with trust graduation.

The dark-factory pattern runs autonomously right up until it hits a
high-stakes action — publish a post, merge a PR, send a wire, email a
parent, rebalance a portfolio. At that boundary we post a request to
the tenant's control channel and wait for a yes/no.

In CI and local demos, we don't have a real reviewer. The gate auto-
approves after a short delay unless the test explicitly wants to
reject. Live mode (``FACTORIES_LIVE=1``) is expected to route through
the tenant's Slack connector, but the gate doesn't own that plumbing —
the caller supplies a ``notifier`` callback.

A tenant can graduate past the gate for specific action kinds once
their trust score crosses a threshold — that piggy-backs on
``sagewai.core.trust``.
"""

from __future__ import annotations

import asyncio
import datetime
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable


class GateDecision(str, Enum):
    """What the human (or CI auto-approver) said."""

    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto-approved"
    GRADUATED = "graduated"  # trust crossed the threshold, no prompt


@dataclass
class GateRequest:
    """One request pending approval."""

    tenant: str
    work_item_id: str
    action: str
    severity: int
    summary: str
    requested_at: str = field(
        default_factory=lambda: datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
    )


@dataclass
class GateOutcome:
    """What came back from the gate."""

    decision: GateDecision
    reason: str = ""


NotifierFn = Callable[[GateRequest], Awaitable[GateOutcome]]


async def _auto_approve_notifier(request: GateRequest) -> GateOutcome:
    """CI default: brief delay, then approve."""
    await asyncio.sleep(0.01)
    return GateOutcome(
        decision=GateDecision.AUTO_APPROVED,
        reason=(
            f"mock-approved for {request.tenant}/{request.action} "
            f"(severity={request.severity})"
        ),
    )


class ApprovalGate:
    """Wraps risky actions behind a human-in-the-loop checkpoint.

    Parameters
    ----------
    tenant:
        Project slug the gate belongs to. Used for scoring + logging.
    notifier:
        Async callable that delivers the request to a human and resolves
        with a decision. Defaults to an auto-approver suitable for CI.
    severity_threshold:
        Actions with severity at or above this threshold trigger the
        notifier. Lower-severity actions pass straight through.
    trust_threshold:
        If a per-action trust score (0-100) exceeds this, the gate will
        return ``GRADUATED`` without bothering the notifier. The caller
        is responsible for feeding the score in via :meth:`record_trust`
        (or by integrating with ``sagewai.core.trust``).
    """

    def __init__(
        self,
        tenant: str,
        *,
        notifier: NotifierFn | None = None,
        severity_threshold: int = 3,
        trust_threshold: int = 80,
    ) -> None:
        self.tenant = tenant
        self._notifier = notifier or _auto_approve_notifier
        self.severity_threshold = severity_threshold
        self.trust_threshold = trust_threshold
        self._trust: dict[str, int] = {}
        self._history: list[tuple[GateRequest, GateOutcome]] = []

    # ── trust graduation ─────────────────────────────────────────

    def record_trust(self, action: str, score: int) -> None:
        """Record a trust score (0-100) for ``action`` on this tenant."""
        self._trust[action] = max(0, min(100, score))

    def trust(self, action: str) -> int:
        return self._trust.get(action, 0)

    # ── the gate itself ──────────────────────────────────────────

    async def check(
        self,
        *,
        work_item_id: str,
        action: str,
        severity: int,
        summary: str,
    ) -> GateOutcome:
        """Ask the notifier for a decision (or short-circuit)."""
        if severity < self.severity_threshold:
            outcome = GateOutcome(
                decision=GateDecision.AUTO_APPROVED,
                reason=f"severity {severity} below threshold",
            )
            self._record(work_item_id, action, severity, summary, outcome)
            return outcome

        if self.trust(action) >= self.trust_threshold:
            outcome = GateOutcome(
                decision=GateDecision.GRADUATED,
                reason=(
                    f"trust {self.trust(action)} ≥ {self.trust_threshold}"
                ),
            )
            self._record(work_item_id, action, severity, summary, outcome)
            return outcome

        request = GateRequest(
            tenant=self.tenant,
            work_item_id=work_item_id,
            action=action,
            severity=severity,
            summary=summary,
        )
        outcome = await self._notifier(request)
        self._history.append((request, outcome))
        return outcome

    # ── history + introspection ──────────────────────────────────

    def history(self) -> list[tuple[GateRequest, GateOutcome]]:
        return list(self._history)

    def _record(
        self,
        work_item_id: str,
        action: str,
        severity: int,
        summary: str,
        outcome: GateOutcome,
    ) -> None:
        request = GateRequest(
            tenant=self.tenant,
            work_item_id=work_item_id,
            action=action,
            severity=severity,
            summary=summary,
        )
        self._history.append((request, outcome))


def default_notifier() -> NotifierFn:
    """Pick a notifier based on env.

    ``FACTORIES_LIVE=1`` expects the caller to wire a real Slack notifier
    from the outside. Until then we fall back to the auto-approver — the
    factory logs will make it obvious which path was used.
    """
    if os.environ.get("FACTORIES_LIVE", "") != "1":
        return _auto_approve_notifier
    return _auto_approve_notifier
