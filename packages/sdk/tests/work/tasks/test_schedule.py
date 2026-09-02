# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cron validation, timezone-aware next fire, and presets."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.work.tasks.schedule import (
    InvalidCronError,
    expand_field,
    next_fire,
    preset_to_cron,
    validate_cron,
    validate_timezone,
)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_validate_cron_accepts_five_fields_and_normalizes_whitespace() -> None:
    assert validate_cron("  0  8 * * 1-5 ") == "0 8 * * 1-5"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0 8 * *",
        "0 8 * * * *",
        "60 8 * * *",
        "0 24 * * *",
        "0 8 0 * *",
        "0 8 * 13 *",
        "0 8 * * 8",
        "5-3 * * * *",
        "*/0 * * * *",
        "a b c d e",
    ],
)
def test_validate_cron_rejects_malformed_or_out_of_range(value: str) -> None:
    with pytest.raises(InvalidCronError):
        validate_cron(value)


def test_expand_field_handles_star_ranges_lists_and_steps() -> None:
    assert expand_field("*", 0, 3) == frozenset({0, 1, 2, 3})
    assert expand_field("1-3", 0, 5) == frozenset({1, 2, 3})
    assert expand_field("1,4", 0, 5) == frozenset({1, 4})
    assert expand_field("*/2", 0, 5) == frozenset({0, 2, 4})
    assert expand_field("1-5/2", 0, 9) == frozenset({1, 3, 5})
    assert expand_field("0/15", 0, 59) == frozenset({0, 15, 30, 45})
    assert expand_field("10/5", 0, 59) == frozenset(range(10, 60, 5))


def test_validate_timezone_rejects_unknown_zone() -> None:
    assert validate_timezone("Europe/Berlin") == "Europe/Berlin"
    with pytest.raises(ValueError):
        validate_timezone("Mars/Olympus")


def test_next_fire_is_strictly_after_and_returns_utc() -> None:
    at_fire = _utc(2026, 6, 1, 6, 0)  # 08:00 CEST
    fire = next_fire("0 8 * * *", after=at_fire, timezone_name="Europe/Berlin")
    assert fire == _utc(2026, 6, 2, 6, 0)
    assert fire.tzinfo is timezone.utc


def test_next_fire_weekday_filter_uses_local_weekday() -> None:
    # 2026-06-05 is a Friday. Next weekday-9am after Friday 09:00 CEST is Monday.
    fire = next_fire("0 9 * * 1-5", after=_utc(2026, 6, 5, 7, 0), timezone_name="Europe/Berlin")
    assert fire == _utc(2026, 6, 8, 7, 0)


def test_next_fire_treats_restricted_day_of_month_and_weekday_as_either() -> None:
    # POSIX cron: when both fields are restricted, the day matches if EITHER matches.
    # 2026-06-12 is a Friday; 2026-06-13 is a Saturday.
    first = next_fire("0 8 13 * 5", after=_utc(2026, 6, 8, 12, 0), timezone_name="UTC")
    assert first == _utc(2026, 6, 12, 8, 0)
    second = next_fire("0 8 13 * 5", after=first, timezone_name="UTC")
    assert second == _utc(2026, 6, 13, 8, 0)


def test_next_fire_skips_nonexistent_spring_forward_time() -> None:
    # EU DST starts 2026-03-29 02:00 CET -> 03:00 CEST; 02:30 does not exist that day.
    fire = next_fire("30 2 * * *", after=_utc(2026, 3, 28, 20, 0), timezone_name="Europe/Berlin")
    assert fire == _utc(2026, 3, 30, 0, 30)  # 02:30 CEST on the 30th


def test_next_fire_fires_once_during_repeated_fall_back_hour() -> None:
    # EU DST ends 2026-10-25 03:00 CEST -> 02:00 CET; 02:30 occurs twice.
    first = next_fire("30 2 * * *", after=_utc(2026, 10, 24, 20, 0), timezone_name="Europe/Berlin")
    assert first == _utc(2026, 10, 25, 0, 30)  # first occurrence, CEST
    second = next_fire("30 2 * * *", after=first, timezone_name="Europe/Berlin")
    assert second == _utc(2026, 10, 26, 1, 30)  # next day, CET; the repeated 02:30 is skipped


def test_next_fire_rejects_naive_after() -> None:
    with pytest.raises(ValueError):
        next_fire("0 8 * * *", after=datetime(2026, 6, 1, 6, 0), timezone_name="UTC")


def test_presets_compile_to_cron() -> None:
    assert preset_to_cron("daily", at="08:00") == "0 8 * * *"
    assert preset_to_cron("weekdays", at="09:30") == "30 9 * * 1-5"
    assert preset_to_cron("weekly", at="07:15", weekday="mon") == "15 7 * * 1"
    assert preset_to_cron("hourly") == "0 * * * *"
    with pytest.raises(ValueError):
        preset_to_cron("fortnightly")
    with pytest.raises(ValueError):
        preset_to_cron("daily", at="25:00")
