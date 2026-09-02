# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Feed entries and in-process fan-out."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from sagewai.work.tasks.feed import FeedBus, FeedEntry

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _entry(sequence: int, task_id: str = "task-1") -> FeedEntry:
    return FeedEntry(
        project_id="project-a",
        task_id=task_id,
        feed_sequence=sequence,
        source="task_event",
        source_id=f"event-{sequence}",
        event_type="TASK_CREATED",
        payload_json={},
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_bus_fans_out_to_task_subscribers_only() -> None:
    bus = FeedBus()
    queue_a = bus.subscribe("project-a", "task-1")
    queue_b = bus.subscribe("project-a", "task-2")
    await bus.publish(_entry(1))
    assert (await asyncio.wait_for(queue_a.get(), 1)).feed_sequence == 1
    assert queue_b.empty()
    queue_c = bus.subscribe("project-a", "task-1")
    await bus.publish(_entry(2))
    assert queue_a.get_nowait().feed_sequence == 2
    assert queue_c.get_nowait().feed_sequence == 2
    bus.unsubscribe("project-a", "task-1", queue_a)
    bus.unsubscribe("project-a", "task-1", queue_c)
    await bus.publish(_entry(3))
    assert queue_a.empty() and queue_c.empty()
    assert bus.dropped("project-a", "task-1") == 0


@pytest.mark.asyncio
async def test_bus_drops_when_subscriber_queue_is_full() -> None:
    bus = FeedBus(max_queue=1)
    queue = bus.subscribe("project-a", "task-1")
    await bus.publish(_entry(1))
    await bus.publish(_entry(2))
    assert queue.qsize() == 1
    assert bus.dropped("project-a", "task-1") == 1
