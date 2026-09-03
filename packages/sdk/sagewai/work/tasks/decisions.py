# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Gate resolution and the decision-channel seam (spec sections 8.8 approval, 15)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from sagewai.work.models import ActionRequest, GateDecision, Reversibility
from sagewai.work.tasks.models import Authority, GateMode

_NEVER_GATES = frozenset({Reversibility.PURE, Reversibility.SNAPSHOT_REVERSIBLE})


def resolve_gate(mode: GateMode, action: ActionRequest) -> GateDecision:
    """Irreversible always asks; compensatable runs once its rollback and post-check exist."""
    if mode is GateMode.AUTO:
        return GateDecision.ALLOW
    if mode is GateMode.REQUIRE:
        return GateDecision.REQUIRE_APPROVAL
    if action.reversibility in _NEVER_GATES:
        return GateDecision.ALLOW
    if (
        action.reversibility is Reversibility.COMPENSATABLE
        and action.rollback
        and action.post_check
    ):
        return GateDecision.ALLOW
    return GateDecision.REQUIRE_APPROVAL


def merge_policy_for(authority: Authority) -> Callable[[ActionRequest], GateDecision]:
    """The GitHubIssueLifecycle merge_policy bound to one Task's authority (section 8.5)."""

    def policy(request: ActionRequest) -> GateDecision:
        return resolve_gate(authority.merge, request)

    return policy


def coordinator_action(project_id: str, *, action: str, work_id: str, scope: str) -> ActionRequest:
    """The action record for a coordinator decision with no external side effect."""
    return ActionRequest(
        project_id=project_id,
        action=action,
        work_id=work_id,
        risk="low",
        reversibility=Reversibility.PURE,
        scope=scope,
        evidence_refs=(),
    )


class DecisionRequest(BaseModel):
    """One `Needs you` item routed to the project's channels."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    task_id: str
    attention_id: str
    summary: str
    urgency: Literal["now", "today", "this_week"]
    due_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()


class DecisionChannel(Protocol):
    """PR4b adds github_issue, slack_webhook, and google_chat_webhook behind this."""

    @property
    def name(self) -> str: ...

    async def notify(self, decision: DecisionRequest) -> str | None: ...


class ConsoleDecisionChannel:
    """Always configured; the console reads the same item from the attention feed."""

    name = "console"

    async def notify(self, decision: DecisionRequest) -> str | None:
        return f"console:{decision.task_id}:{decision.attention_id}"


class NullDecisionScheduler:
    """DECISION_SCHEDULED is recorded only when a scheduler books; this one never does."""

    async def book(self, decision: DecisionRequest) -> str | None:
        return None


__all__ = [
    "ConsoleDecisionChannel",
    "DecisionChannel",
    "DecisionRequest",
    "NullDecisionScheduler",
    "coordinator_action",
    "merge_policy_for",
    "resolve_gate",
]
