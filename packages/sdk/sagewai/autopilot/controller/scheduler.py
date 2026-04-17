# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Cron-based mission scheduler for the autopilot framework.

:class:`MissionScheduler`
    Registers missions against their cron expressions, fires them at the
    right time via :meth:`tick`, and tracks run history.

:class:`ScheduledMission`
    Frozen Pydantic model describing one scheduled mission's state.

:class:`CronParser`
    Minimal 5-field POSIX cron evaluator.  No external dependency.
    Supports ``*``, ``*/N``, ``N``, ``N-M``, and ``N,M`` in each field.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sagewai.autopilot.mission import Mission

logger = logging.getLogger(__name__)

# ── CronParser ────────────────────────────────────────────────────────────────

_FIELD_BOUNDS = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0 = Sunday … 6 = Saturday; 7 also means Sunday per POSIX)
]


def _expand_field(token: str, lo: int, hi: int) -> frozenset[int]:
    """Return the full set of integers matched by *token* in range [lo, hi]."""
    result: set[int] = set()
    for part in token.split(","):
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)

        if part == "*":
            values = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            values = range(int(a), int(b) + 1)
        else:
            values = range(int(part), int(part) + 1)

        result.update(v for v in values if v % step == (lo % step) or part == "*")

        # Re-apply step correctly for * and ranges
        if step > 1:
            result.discard(0)  # clean up artefacts from above
            result.update(range(list(values)[0], hi + 1, step))
        else:
            result.update(values)

    return frozenset(v for v in result if lo <= v <= hi)


def _expand_field_clean(token: str, lo: int, hi: int) -> frozenset[int]:
    """Correct expansion for a single comma-separated cron field token."""
    result: set[int] = set()
    for part in token.split(","):
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)

        if part == "*":
            base = list(range(lo, hi + 1))
        elif "-" in part:
            a, b = part.split("-", 1)
            base = list(range(int(a), int(b) + 1))
        else:
            base = [int(part)]

        if step > 1:
            result.update(base[i] for i in range(0, len(base), step))
        else:
            result.update(base)

    return frozenset(v for v in result if lo <= v <= hi)


class CronParser:
    """Minimal 5-field POSIX cron expression evaluator.

    Only ``next_fire`` is the public method.  Builds allowed-value sets
    for each field and advances *after* one minute at a time until a
    matching timestamp is found (capped at 4 years to avoid infinite
    loops on malformed expressions that pass syntax validation).
    """

    # Maximum search window to avoid runaway loops
    _MAX_SEARCH_DAYS = 366 * 4

    @staticmethod
    def next_fire(cron: str, after: datetime) -> datetime:
        """Return the first datetime strictly after *after* matching *cron*.

        Args:
            cron: A validated 5-field POSIX cron expression.
            after: The reference time.  The returned time will be strictly
                greater than *after*.

        Returns:
            The next matching :class:`datetime` with second=0, microsecond=0,
            preserving the timezone info of *after*.

        Raises:
            ValueError: If *cron* has the wrong field count or no firing
                time is found within the 4-year search window.
        """
        fields = cron.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression must have 5 fields, got {len(fields)}")

        minutes_f, hours_f, doms_f, months_f, dows_f = fields
        bounds = _FIELD_BOUNDS

        minutes  = _expand_field_clean(minutes_f,  *bounds[0])
        hours    = _expand_field_clean(hours_f,    *bounds[1])
        doms     = _expand_field_clean(doms_f,     *bounds[2])
        months   = _expand_field_clean(months_f,   *bounds[3])
        # Normalize day-of-week: 7 → 0 (both mean Sunday)
        raw_dows = _expand_field_clean(dows_f, 0, 7)
        dows = frozenset(d % 7 for d in raw_dows)

        # Start searching one minute after *after*
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        deadline  = after + timedelta(days=CronParser._MAX_SEARCH_DAYS)

        while candidate <= deadline:
            if candidate.month not in months:
                # Jump to next matching month (first day, 00:00)
                m = candidate.month + 1
                y = candidate.year
                if m > 12:
                    m = 1
                    y += 1
                # Find next valid month
                for _ in range(13):
                    if m > 12:
                        m = 1
                        y += 1
                    if m in months:
                        break
                    m += 1
                candidate = candidate.replace(year=y, month=m, day=1, hour=0, minute=0)
                continue

            if candidate.day not in doms:
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
                continue

            # day-of-week check (isoweekday: Mon=1..Sun=7 → convert to 0=Sun..6=Sat)
            iso_dow = candidate.isoweekday()  # 1=Mon..7=Sun
            dow = iso_dow % 7  # 0=Sun, 1=Mon, …, 6=Sat
            if dow not in dows:
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
                continue

            if candidate.hour not in hours:
                candidate = candidate.replace(minute=0) + timedelta(hours=1)
                continue

            if candidate.minute not in minutes:
                candidate = candidate + timedelta(minutes=1)
                continue

            return candidate

        raise ValueError(
            f"No firing time found for cron {cron!r} within {CronParser._MAX_SEARCH_DAYS} days"
        )


# ── ScheduledMission ─────────────────────────────────────────────────────────


class ScheduledMission(BaseModel):
    """Immutable record of a mission registered with the scheduler."""

    model_config = ConfigDict(frozen=True)

    mission_id: str = Field(min_length=1)
    cron_expression: str = Field(min_length=1)
    next_run_at: datetime
    last_run_at: datetime | None = None
    run_count: int = Field(default=0, ge=0)
    paused: bool = False


# ── MissionScheduler ─────────────────────────────────────────────────────────


class MissionScheduler:
    """Manages recurring mission execution via cron schedules.

    Missions are registered with :meth:`schedule` and dequeued for
    execution by calling :meth:`tick`.  Tick is intentionally *pull-based*
    so that callers control the event loop (useful in tests and async
    drivers alike).

    All scheduling state is in-memory; no persistence is provided here.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ScheduledMission] = {}

    # ── public interface ──────────────────────────────────────────────

    def schedule(self, mission: Mission) -> ScheduledMission:
        """Register *mission* with its cron expression (read from slots).

        The cron expression is expected in ``mission.slots["schedule"]``.
        If the slot is absent, ``"* * * * *"`` (every minute) is used as
        a fallback so that tests do not need to supply it explicitly.

        Args:
            mission: A mission whose slots contain a ``"schedule"`` key
                with a valid 5-field cron expression.

        Returns:
            The :class:`ScheduledMission` entry added to the registry.
        """
        cron = str(mission.slots.get("schedule", "* * * * *"))
        now = datetime.now(tz=timezone.utc)
        next_run = CronParser.next_fire(cron, now)
        entry = ScheduledMission(
            mission_id=mission.mission_id,
            cron_expression=cron,
            next_run_at=next_run,
        )
        self._entries[mission.mission_id] = entry
        logger.info(
            "Mission %s scheduled (cron=%r, next_run_at=%s)",
            mission.mission_id,
            cron,
            next_run.isoformat(),
        )
        return entry

    def get_next_run(self, mission_id: str) -> datetime | None:
        """Return the next fire time for *mission_id*, or ``None`` if not found."""
        entry = self._entries.get(mission_id)
        if entry is None:
            return None
        return entry.next_run_at

    def list_scheduled(self) -> list[ScheduledMission]:
        """Return all registered (including paused) scheduled missions."""
        return list(self._entries.values())

    def cancel(self, mission_id: str) -> bool:
        """Remove *mission_id* from the schedule.

        Returns:
            ``True`` if the mission was found and removed, ``False`` if
            it was not registered.
        """
        if mission_id in self._entries:
            del self._entries[mission_id]
            logger.info("Mission %s cancelled from schedule.", mission_id)
            return True
        return False

    def tick(self, now: datetime | None = None) -> list[Mission]:
        """Return missions whose ``next_run_at`` is at or before *now*.

        For each fired mission, ``next_run_at`` is advanced to the next
        cron firing time after *now*, ``last_run_at`` is set to *now*,
        and ``run_count`` is incremented.  Paused missions are skipped.

        Args:
            now: The clock instant to use.  Defaults to
                :func:`datetime.now(tz=timezone.utc)`.

        Returns:
            A list of :class:`Mission` stubs — one per fired entry.
            The stubs only carry ``mission_id``; callers are expected to
            look up the real mission objects from their own store.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)

        fired: list[Mission] = []
        for mid, entry in list(self._entries.items()):
            if entry.paused:
                continue
            if entry.next_run_at <= now:
                # Compute next run after *now*
                next_run = CronParser.next_fire(entry.cron_expression, now)
                updated = ScheduledMission(
                    mission_id=entry.mission_id,
                    cron_expression=entry.cron_expression,
                    next_run_at=next_run,
                    last_run_at=now,
                    run_count=entry.run_count + 1,
                    paused=entry.paused,
                )
                self._entries[mid] = updated
                # Return a lightweight Mission stub with mission_id only
                fired.append(_make_stub_mission(entry.mission_id))
                logger.info(
                    "Mission %s fired at %s; next_run_at=%s",
                    entry.mission_id,
                    now.isoformat(),
                    next_run.isoformat(),
                )
        return fired

    def pause(self, mission_id: str) -> bool:
        """Pause a scheduled mission so tick() will not fire it.

        Returns:
            ``True`` if found and paused, ``False`` if not registered.
        """
        entry = self._entries.get(mission_id)
        if entry is None:
            return False
        self._entries[mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression=entry.cron_expression,
            next_run_at=entry.next_run_at,
            last_run_at=entry.last_run_at,
            run_count=entry.run_count,
            paused=True,
        )
        return True


# ── internal helper ───────────────────────────────────────────────────────────


def _make_stub_mission(mission_id: str) -> Mission:
    """Create a minimal Mission carrying only *mission_id* for tick() callers."""
    return Mission(
        mission_id=mission_id,
        project_id="__scheduler__",
        blueprint_id="__stub__",
        blueprint_version="0",
        slots={},
    )
