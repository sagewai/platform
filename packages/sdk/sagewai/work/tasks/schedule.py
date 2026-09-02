# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cron validation, timezone-aware next fire, and schedule presets for Tasks."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FIELD_BOUNDS: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_TOKEN_RE = re.compile(r"^(\*|\d+(-\d+)?)(/\d+)?(,(\*|\d+(-\d+)?)(/\d+)?)*$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MAX_SEARCH_DAYS = 366 * 4
_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


class InvalidCronError(ValueError):
    """The cron expression is malformed or never fires."""


def validate_cron(value: str) -> str:
    """Accept a 5-field POSIX cron expression and return it normalised."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidCronError("cron expression must be a non-empty string")
    fields = value.split()
    if len(fields) != 5:
        raise InvalidCronError(f"cron expression must have 5 fields, got {len(fields)}")
    for token, (lo, hi) in zip(fields, _FIELD_BOUNDS):
        if _TOKEN_RE.match(token) is None:
            raise InvalidCronError(f"invalid cron token: {token!r}")
        for part in token.split(","):
            base, _, step = part.partition("/")
            if step and int(step) < 1:
                raise InvalidCronError(f"cron step must be positive: {token!r}")
            if base == "*":
                continue
            first, _, last = base.partition("-")
            low, high = int(first), int(last) if last else int(first)
            if low > high or not (lo <= low <= hi and lo <= high <= hi):
                raise InvalidCronError(f"cron field {token!r} out of range [{lo},{hi}]")
    return " ".join(fields)


def expand_field(token: str, lo: int, hi: int) -> frozenset[int]:
    """Return every value in [lo, hi] matched by one cron field token; ``A/N`` means ``A-hi/N``."""
    result: set[int] = set()
    for part in token.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if base == "*":
            start, stop = lo, hi
        elif "-" in base:
            first, last = base.split("-", 1)
            start, stop = int(first), int(last)
        else:
            start = int(base)
            stop = hi if step_text else start
        result.update(range(start, stop + 1, step))
    return frozenset(value for value in result if lo <= value <= hi)


def validate_timezone(name: str) -> str:
    """Accept an IANA timezone name."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"unknown timezone: {name!r}") from exc
    return name


def _localize_once(naive: datetime, zone: ZoneInfo) -> datetime | None:
    """Attach ``zone`` to a wall-clock time; None when that wall time does not exist."""
    localized = naive.replace(tzinfo=zone, fold=0)
    round_trip = localized.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        return None
    return localized


def next_fire(cron: str, *, after: datetime, timezone_name: str) -> datetime:
    """Return the first UTC instant strictly after ``after`` at which ``cron`` fires.

    Wall-clock times that do not exist (spring forward) are skipped; wall-clock
    times that occur twice (fall back) fire once, at their first occurrence.
    When both the day-of-month and the day-of-week fields are restricted, a day
    matches if either matches, as in POSIX cron.
    """
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    expression = validate_cron(cron)
    zone = ZoneInfo(validate_timezone(timezone_name))
    minutes_f, hours_f, doms_f, months_f, dows_f = expression.split()
    minutes = expand_field(minutes_f, 0, 59)
    hours = expand_field(hours_f, 0, 23)
    doms = expand_field(doms_f, 1, 31)
    months = expand_field(months_f, 1, 12)
    dows = frozenset(day % 7 for day in expand_field(dows_f, 0, 7))
    dom_restricted = doms_f != "*"
    dow_restricted = dows_f != "*"

    local_after = after.astimezone(zone)
    candidate = local_after.replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=_MAX_SEARCH_DAYS)
    while candidate <= deadline:
        if candidate.month not in months:
            month, year = candidate.month + 1, candidate.year
            if month > 12:
                month, year = 1, year + 1
            candidate = candidate.replace(year=year, month=month, day=1, hour=0, minute=0)
            continue
        day_matches = candidate.day in doms
        weekday_matches = (candidate.isoweekday() % 7) in dows
        if dom_restricted and dow_restricted:
            day_ok = day_matches or weekday_matches
        else:
            day_ok = day_matches and weekday_matches
        if not day_ok:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if candidate.hour not in hours:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
            continue
        if candidate.minute not in minutes:
            candidate += timedelta(minutes=1)
            continue
        localized = _localize_once(candidate, zone)
        if localized is None:
            candidate += timedelta(minutes=1)
            continue
        instant = localized.astimezone(timezone.utc)
        if instant <= after:
            candidate += timedelta(minutes=1)
            continue
        return instant
    raise InvalidCronError(f"no firing time found for cron {expression!r} within {_MAX_SEARCH_DAYS} days")


def preset_to_cron(preset: str, *, at: str = "08:00", weekday: str | None = None) -> str:
    """Compile a console preset into a cron expression."""
    match = _TIME_RE.match(at)
    if match is None:
        raise ValueError(f"time must be HH:MM, got {at!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if preset == "hourly":
        return f"{minute} * * * *"
    if preset == "daily":
        return f"{minute} {hour} * * *"
    if preset == "weekdays":
        return f"{minute} {hour} * * 1-5"
    if preset == "weekly":
        day = _WEEKDAYS.get((weekday or "mon").lower())
        if day is None:
            raise ValueError(f"unknown weekday: {weekday!r}")
        return f"{minute} {hour} * * {day}"
    raise ValueError(f"unknown schedule preset: {preset!r}")


__all__ = [
    "InvalidCronError",
    "expand_field",
    "next_fire",
    "preset_to_cron",
    "validate_cron",
    "validate_timezone",
]
