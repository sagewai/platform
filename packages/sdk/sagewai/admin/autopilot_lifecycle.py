# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Mission lifecycle state machine for the admin panel.

All status mutations must go through :func:`transition_mission` — it is
the single source of truth for valid transitions.  Calling code that
bypasses this function and writes ``status`` directly will silently
produce an inconsistent mission record.

State diagram::

    pending ──► running ──► completed
         │          │
         │          ├──► failed
         │          └──► cancelled
         └──────────────► cancelled
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.admin.state_file import AdminStateFile


class MissionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED: dict[MissionStatus, set[MissionStatus]] = {
    MissionStatus.PENDING: {MissionStatus.RUNNING, MissionStatus.CANCELLED},
    MissionStatus.RUNNING: {
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    },
    MissionStatus.COMPLETED: set(),
    MissionStatus.FAILED: set(),
    MissionStatus.CANCELLED: set(),
}


class IllegalTransition(ValueError):
    """Raised when a state transition is not allowed."""


def assert_transition(old: MissionStatus, new: MissionStatus) -> None:
    """Raise :class:`IllegalTransition` if *old → new* is not permitted."""
    if new not in _ALLOWED[old]:
        raise IllegalTransition(f"{old.value} → {new.value} not allowed")


def transition_mission(
    sf: AdminStateFile,
    mission_id: str,
    new_status: MissionStatus,
    *,
    reason: str | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Apply a status transition to the named mission and persist it.

    Parameters
    ----------
    sf:
        The admin state file store.
    mission_id:
        The mission to update.
    new_status:
        The target status.  Must be a valid transition from the current status.
    reason:
        Optional human-readable reason.  Used as ``cancel_reason`` on
        CANCELLED and ``failure_reason`` on FAILED.
    now:
        Override the current UTC timestamp (useful in tests).

    Returns
    -------
    dict
        The updated mission record.

    Raises
    ------
    KeyError
        If no mission with *mission_id* exists.
    IllegalTransition
        If the requested status change is not permitted.
    """
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        missions: list[dict[str, Any]] = data.get("autopilot_missions", [])
        for mission in missions:
            if mission.get("mission_id") == mission_id:
                old_raw = mission.get("status", "pending")
                try:
                    old = MissionStatus(old_raw)
                except ValueError:
                    raise IllegalTransition(f"unknown current status '{old_raw}'")
                assert_transition(old, new_status)
                mission["status"] = new_status.value
                if new_status is MissionStatus.RUNNING:
                    mission["started_at"] = ts
                elif new_status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                    mission["finished_at"] = ts
                    if new_status is MissionStatus.FAILED and reason is not None:
                        mission["failure_reason"] = reason
                elif new_status is MissionStatus.CANCELLED:
                    mission["cancelled_at"] = ts
                    if reason is not None:
                        mission["cancel_reason"] = reason
                return dict(mission)
        raise KeyError(f"mission '{mission_id}' not found")

    return sf._mutate(_mutate)
