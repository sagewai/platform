# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Error hierarchy for Sagewai Autopilot.

All autopilot-specific exceptions inherit from :class:`AutopilotError`,
which in turn inherits from :class:`sagewai.errors.SagewaiError`. This
lets callers write a single ``except AutopilotError`` to catch any
framework failure while still allowing fine-grained handling.
"""

from __future__ import annotations

from sagewai.errors import SagewaiError


class AutopilotError(SagewaiError):
    """Base class for all Sagewai Autopilot framework errors."""


class SlotValidationError(AutopilotError):
    """Raised when a slot value fails validation.

    Attributes:
        slot_name: Name of the slot that failed.
        reason:    Human-readable reason.
    """

    def __init__(self, slot_name: str, reason: str) -> None:
        self.slot_name = slot_name
        self.reason = reason
        super().__init__(f"slot {slot_name!r}: {reason}")


class BlueprintValidationError(AutopilotError):
    """Raised when a blueprint fails structural validation."""


class AgentGraphError(AutopilotError):
    """Raised when an agent graph is malformed or cannot be traversed.

    Attributes:
        node_id: Optional node identifier this error relates to.
    """

    def __init__(self, reason: str, *, node_id: str | None = None) -> None:
        self.node_id = node_id
        if node_id is not None:
            super().__init__(f"agent graph error at node {node_id!r}: {reason}")
        else:
            super().__init__(f"agent graph error: {reason}")


class MissionLifecycleError(AutopilotError):
    """Raised when a mission state transition is not allowed.

    Attributes:
        from_state: Current state name.
        to_state:   Requested state name.
    """

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"illegal mission transition: {from_state!r} -> {to_state!r}")


class NoWorkerAvailableError(AutopilotError):
    """Raised when no fleet worker satisfies an agent step's capability requirements.

    Attributes:
        agent_id: The agent node id that could not be matched.
        unmet_labels: Tool labels not covered by any worker.
        unmet_models: Canonical model names not covered by any worker.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        unmet_labels: list[str] | None = None,
        unmet_models: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.unmet_labels = unmet_labels or []
        self.unmet_models = unmet_models or []
        super().__init__(
            f"no fleet worker available for agent {agent_id!r}: "
            f"unmet_labels={self.unmet_labels}, unmet_models={self.unmet_models}"
        )
