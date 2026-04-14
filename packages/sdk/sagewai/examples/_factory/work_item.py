# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""The canonical dark-factory work item.

Every factory example wraps its inbound brief (Slack message, email, cron
tick, CSV row) in a :class:`WorkItem` before enqueueing it on the
dispatcher. That gives us one shape to carry through the fleet scoreboard,
the approval gate, and the training sample collector.
"""

from __future__ import annotations

import datetime
import secrets
from dataclasses import dataclass, field
from typing import Any


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _short_id() -> str:
    return secrets.token_hex(4)


@dataclass
class WorkItem:
    """A unit of work owned by exactly one tenant.

    Attributes
    ----------
    tenant:
        Project slug the item belongs to (``app-factory``, ``biz-ops`` …).
        Copied onto every dispatcher task so the fleet can scope-match.
    channel:
        Intake channel that delivered the brief (``slack``, ``email``,
        ``cron``, ``lms``, ``portfolio-csv`` …). Useful for analytics and
        for deciding which ``ApprovalGate`` reply path to use.
    brief:
        The raw human-readable input. Factories parse this themselves.
    priority:
        1 (urgent) through 5 (background). Used as a tiebreaker by the
        dispatcher when workers are saturated.
    metadata:
        Free-form dict for domain extras — campaign id, student id,
        portfolio slug, prospect email, etc.
    id, created_at:
        Auto-assigned if not supplied. ``id`` is short + URL-safe so it
        can show up in filesystem paths like ``artifacts/<tenant>/<id>/``.
    """

    tenant: str
    channel: str
    brief: str
    priority: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_short_id)
    created_at: str = field(default_factory=_iso_now)

    def __post_init__(self) -> None:
        if not self.tenant:
            raise ValueError("WorkItem.tenant is required")
        if not 1 <= self.priority <= 5:
            raise ValueError(
                f"priority must be in [1, 5], got {self.priority}"
            )

    def to_task_metadata(self) -> dict[str, Any]:
        """Flatten this item into the metadata dict the fleet dispatcher
        carries on every task. Includes ``project_id`` so workers match.
        """
        return {
            "project_id": self.tenant,
            "work_item_id": self.id,
            "channel": self.channel,
            "priority": self.priority,
            "brief": self.brief,
            **self.metadata,
        }
