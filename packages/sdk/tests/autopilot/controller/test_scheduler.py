# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MissionScheduler, CronParser, and ScheduledMission."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.controller.scheduler import CronParser, MissionScheduler
from tests.autopilot.controller.conftest import (
    _advance_to_scheduled,
    _make_mission_from_blueprint,
)
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_scheduled_blueprint,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _dt(y, mo, d, h, mi, tz=timezone.utc) -> datetime:
    return datetime(y, mo, d, h, mi, 0, 0, tzinfo=tz)


# ── CronParser.next_fire ──────────────────────────────────────────────────────


class TestCronParserWeekdayMornings:
    """'0 9 * * 1-5'  — fires Mon-Fri at 09:00."""

    CRON = "0 9 * * 1-5"

    def test_fires_on_next_weekday_morning(self):
        # Friday 08:00 → should fire same day at 09:00
        after = _dt(2026, 4, 10, 8, 0)  # Friday
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 10, 9, 0)

    def test_skips_weekend_from_friday_afternoon(self):
        # Friday 10:00 → next is Monday 09:00
        after = _dt(2026, 4, 10, 10, 0)  # Friday
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 13, 9, 0)  # Monday

    def test_saturday_gives_monday(self):
        after = _dt(2026, 4, 11, 0, 0)  # Saturday midnight
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 13, 9, 0)  # Monday

    def test_minute_is_zero(self):
        after = _dt(2026, 4, 14, 8, 59)  # Tuesday before fire
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt.minute == 0
        assert nxt.hour == 9

    def test_returns_future_time(self):
        after = _dt(2026, 4, 14, 12, 0)  # Tuesday noon
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt > after


class TestCronParserEvery5Min:
    """'*/5 * * * *'  — fires every 5 minutes."""

    CRON = "*/5 * * * *"

    def test_fires_at_next_5_minute_boundary(self):
        after = _dt(2026, 4, 14, 10, 0)
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 14, 10, 5)

    def test_fires_from_offset_minute(self):
        after = _dt(2026, 4, 14, 10, 3)
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 14, 10, 5)

    def test_wraps_across_hour(self):
        after = _dt(2026, 4, 14, 10, 58)
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 14, 11, 0)

    def test_strictly_after_boundary(self):
        # exactly on a 5-min boundary → next one is 5 min later
        after = _dt(2026, 4, 14, 10, 5)
        nxt = CronParser.next_fire(self.CRON, after)
        assert nxt == _dt(2026, 4, 14, 10, 10)

    def test_fires_every_5_minutes_through_day(self):
        """Spot-check that we get 5-min gaps throughout an hour."""
        after = _dt(2026, 4, 14, 14, 0)
        prev = after
        for _ in range(12):
            nxt = CronParser.next_fire(self.CRON, prev)
            diff = (nxt - prev).total_seconds()
            assert diff == 300, f"Expected 5-min gap, got {diff}s"
            prev = nxt


class TestCronParserEdgeCases:
    def test_invalid_field_count_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            CronParser.next_fire("* * * *", _dt(2026, 4, 14, 0, 0))

    def test_single_fixed_time_fires_correctly(self):
        # "30 8 15 4 *" — 08:30 on April 15 every year
        after = _dt(2026, 4, 14, 0, 0)
        nxt = CronParser.next_fire("30 8 15 4 *", after)
        assert nxt == _dt(2026, 4, 15, 8, 30)


# ── MissionScheduler ──────────────────────────────────────────────────────────


@pytest.fixture()
def scheduler() -> MissionScheduler:
    return MissionScheduler()


@pytest.fixture()
def scheduled_mission():
    bp = make_synthetic_scheduled_blueprint()
    m = _make_mission_from_blueprint(bp)
    _advance_to_scheduled(m)
    return m


class TestMissionSchedulerRegistration:
    def test_schedule_returns_scheduled_mission(self, scheduler, scheduled_mission):
        entry = scheduler.schedule(scheduled_mission)
        assert entry.mission_id == scheduled_mission.mission_id

    def test_scheduled_mission_appears_in_list(self, scheduler, scheduled_mission):
        scheduler.schedule(scheduled_mission)
        ids = [e.mission_id for e in scheduler.list_scheduled()]
        assert scheduled_mission.mission_id in ids

    def test_cron_expression_from_blueprint_slot(self, scheduler, scheduled_mission):
        entry = scheduler.schedule(scheduled_mission)
        # The synthetic scheduled blueprint has default schedule "0 9 * * 1-5"
        assert entry.cron_expression == scheduled_mission.slots["schedule"]

    def test_get_next_run_returns_future_time(self, scheduler, scheduled_mission):
        scheduler.schedule(scheduled_mission)
        next_run = scheduler.get_next_run(scheduled_mission.mission_id)
        assert next_run is not None
        assert next_run > datetime.now(tz=timezone.utc)

    def test_get_next_run_unknown_id_returns_none(self, scheduler):
        assert scheduler.get_next_run("no-such-id") is None


class TestMissionSchedulerCancel:
    def test_cancel_removes_from_list(self, scheduler, scheduled_mission):
        scheduler.schedule(scheduled_mission)
        result = scheduler.cancel(scheduled_mission.mission_id)
        assert result is True
        ids = [e.mission_id for e in scheduler.list_scheduled()]
        assert scheduled_mission.mission_id not in ids

    def test_cancel_unknown_returns_false(self, scheduler):
        assert scheduler.cancel("ghost-id") is False

    def test_cancel_then_get_next_run_returns_none(self, scheduler, scheduled_mission):
        scheduler.schedule(scheduled_mission)
        scheduler.cancel(scheduled_mission.mission_id)
        assert scheduler.get_next_run(scheduled_mission.mission_id) is None


class TestMissionSchedulerTick:
    def test_tick_fires_due_mission(self, scheduler, scheduled_mission):
        """Mission whose next_run_at is in the past should fire."""
        # Override next_run_at via re-scheduling with a past-anchored now
        past = _dt(2026, 1, 1, 0, 0)
        entry = scheduler.schedule(scheduled_mission)
        # Force the entry to have a past next_run_at by directly manipulating
        from sagewai.autopilot.controller.scheduler import ScheduledMission

        scheduler._entries[entry.mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression=entry.cron_expression,
            next_run_at=past,
        )
        now = _dt(2026, 4, 14, 10, 0)
        fired = scheduler.tick(now=now)
        assert any(m.mission_id == scheduled_mission.mission_id for m in fired)

    def test_tick_does_not_fire_future_mission(self, scheduler, scheduled_mission):
        scheduler.schedule(scheduled_mission)
        # tick at a time before next_run_at
        now = _dt(2020, 1, 1, 0, 0)
        fired = scheduler.tick(now=now)
        assert not any(m.mission_id == scheduled_mission.mission_id for m in fired)

    def test_tick_advances_next_run_at(self, scheduler, scheduled_mission):
        """After firing, next_run_at must be strictly greater than now."""
        from sagewai.autopilot.controller.scheduler import ScheduledMission

        past = _dt(2026, 1, 1, 0, 0)
        entry = scheduler.schedule(scheduled_mission)
        scheduler._entries[entry.mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression="*/5 * * * *",
            next_run_at=past,
        )
        now = _dt(2026, 4, 14, 10, 0)
        scheduler.tick(now=now)
        updated = scheduler._entries[entry.mission_id]
        assert updated.next_run_at > now

    def test_tick_increments_run_count(self, scheduler, scheduled_mission):
        from sagewai.autopilot.controller.scheduler import ScheduledMission

        past = _dt(2026, 1, 1, 0, 0)
        entry = scheduler.schedule(scheduled_mission)
        scheduler._entries[entry.mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression="*/5 * * * *",
            next_run_at=past,
            run_count=3,
        )
        now = _dt(2026, 4, 14, 10, 0)
        scheduler.tick(now=now)
        assert scheduler._entries[entry.mission_id].run_count == 4

    def test_tick_updates_last_run_at(self, scheduler, scheduled_mission):
        from sagewai.autopilot.controller.scheduler import ScheduledMission

        past = _dt(2026, 1, 1, 0, 0)
        entry = scheduler.schedule(scheduled_mission)
        scheduler._entries[entry.mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression="*/5 * * * *",
            next_run_at=past,
        )
        now = _dt(2026, 4, 14, 10, 0)
        scheduler.tick(now=now)
        assert scheduler._entries[entry.mission_id].last_run_at == now

    def test_tick_does_not_fire_paused_missions(self, scheduler, scheduled_mission):
        from sagewai.autopilot.controller.scheduler import ScheduledMission

        past = _dt(2026, 1, 1, 0, 0)
        entry = scheduler.schedule(scheduled_mission)
        scheduler._entries[entry.mission_id] = ScheduledMission(
            mission_id=entry.mission_id,
            cron_expression="*/5 * * * *",
            next_run_at=past,
            paused=True,
        )
        now = _dt(2026, 4, 14, 10, 0)
        fired = scheduler.tick(now=now)
        assert not any(m.mission_id == scheduled_mission.mission_id for m in fired)


# ── MissionDriver deferred scheduling ─────────────────────────────────────────


class TestMissionDriverWithScheduler:
    @pytest.mark.asyncio
    async def test_scheduled_mode_mission_is_deferred(self, scheduled_mission):
        """When a scheduler is injected, SCHEDULED-mode missions are deferred."""
        from sagewai.autopilot.controller.driver import MissionDriver

        sched = MissionScheduler()
        driver = MissionDriver(scheduler=sched)
        result = await driver.execute(scheduled_mission)
        assert result.status == "deferred"

    @pytest.mark.asyncio
    async def test_deferred_mission_appears_in_scheduler(self, scheduled_mission):
        """The deferred mission should be registered in the scheduler."""
        from sagewai.autopilot.controller.driver import MissionDriver

        sched = MissionScheduler()
        driver = MissionDriver(scheduler=sched)
        await driver.execute(scheduled_mission)
        ids = [e.mission_id for e in sched.list_scheduled()]
        assert scheduled_mission.mission_id in ids

    @pytest.mark.asyncio
    async def test_no_scheduler_runs_immediately(self, scheduled_mission):
        """Without a scheduler, SCHEDULED-mode missions execute immediately (backward compat)."""
        from sagewai.autopilot.controller.driver import MissionDriver

        driver = MissionDriver()  # no scheduler
        result = await driver.execute(scheduled_mission)
        assert result.status in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_batch_mode_mission_not_deferred(self, scheduler):
        """Batch missions bypass the scheduler even when one is injected."""
        from sagewai.autopilot.controller.driver import MissionDriver
        from sagewai.autopilot._types import MissionState

        bp = make_synthetic_batch_blueprint()
        mission = _make_mission_from_blueprint(bp)
        # Batch blueprint has branches — advance to SCHEDULED
        mission.transition_to(MissionState.APPROVED)
        mission.transition_to(MissionState.SCHEDULED)

        driver = MissionDriver(scheduler=scheduler)
        result = await driver.execute(mission)
        # Should have executed (completed), not deferred
        assert result.status in ("completed", "failed")
        # Scheduler should have nothing registered
        assert scheduler.list_scheduled() == []


# ── Thread-safety ─────────────────────────────────────────────────────────────


def test_scheduler_concurrent_schedule_no_exceptions():
    """Concurrent schedule() calls must not raise and register all missions."""
    import concurrent.futures

    from sagewai.autopilot.controller.scheduler import MissionScheduler
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    scheduler = MissionScheduler()
    errors: list[Exception] = []

    def _schedule(idx: int) -> None:
        mission = _make_mission_from_blueprint(bp, mission_id=f"ms-thread-{idx:04d}")
        _advance_to_scheduled(mission)
        try:
            scheduler.schedule(mission)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_schedule, i) for i in range(40)]
        concurrent.futures.wait(futures)

    assert errors == [], f"Exceptions: {errors}"
    assert len(scheduler.list_scheduled()) == 40


def test_scheduler_concurrent_cancel_no_exceptions():
    """Concurrent cancel() calls while tick() is running must not raise."""
    import concurrent.futures
    from datetime import datetime, timezone

    from sagewai.autopilot.controller.scheduler import MissionScheduler
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    scheduler = MissionScheduler()

    # Pre-populate
    missions = []
    for i in range(20):
        m = _make_mission_from_blueprint(bp, mission_id=f"ms-cancel-{i:04d}")
        _advance_to_scheduled(m)
        scheduler.schedule(m)
        missions.append(m)

    errors: list[Exception] = []
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)

    def _tick() -> None:
        try:
            scheduler.tick(now=past)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def _cancel(mid: str) -> None:
        try:
            scheduler.cancel(mid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        tick_futs = [pool.submit(_tick) for _ in range(5)]
        cancel_futs = [pool.submit(_cancel, m.mission_id) for m in missions]
        concurrent.futures.wait(tick_futs + cancel_futs)

    assert errors == [], f"Exceptions: {errors}"


def test_scheduler_concurrent_pause_no_exceptions():
    """pause() under concurrent tick() must not raise."""
    import concurrent.futures
    from datetime import datetime, timezone

    from sagewai.autopilot.controller.scheduler import MissionScheduler
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    scheduler = MissionScheduler()
    missions = []
    for i in range(10):
        m = _make_mission_from_blueprint(bp, mission_id=f"ms-pause-{i:04d}")
        _advance_to_scheduled(m)
        scheduler.schedule(m)
        missions.append(m)

    errors: list[Exception] = []
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)

    def _tick() -> None:
        try:
            scheduler.tick(now=past)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def _pause(mid: str) -> None:
        try:
            scheduler.pause(mid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_tick) for _ in range(3)]
        futs += [pool.submit(_pause, m.mission_id) for m in missions]
        concurrent.futures.wait(futs)

    assert errors == []
