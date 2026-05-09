# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-process pub/sub bus for mission lifecycle events.

A single :class:`LifecycleBus` instance lives for the lifetime of the
admin server process.  Consumers subscribe via :meth:`LifecycleBus.subscribe`
and receive every :class:`MissionStatusChanged` event published for their
org.  The SSE endpoint in ``autopilot_routes.py`` wraps a subscription in
an ``EventSourceResponse``.

Isolation guarantee: events for org A are never delivered to a subscriber
on org B.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncIterator

from pydantic import BaseModel


class MissionStatusChanged(BaseModel):
    mission_id: str
    old_status: str
    new_status: str
    ts: datetime


class LifecycleBus:
    """Fanout bus keyed by ``org_id``.

    One :class:`asyncio.Queue` is created per subscriber.  Publishing to
    an org places the event on every queue registered under that org.
    Subscribers that disconnect (cancelled) automatically clean up via the
    ``finally`` block in :meth:`subscribe`.
    """

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[MissionStatusChanged]]] = defaultdict(set)

    async def publish(self, org_id: str, event: MissionStatusChanged) -> None:
        for q in list(self._subs[org_id]):
            await q.put(event)

    async def subscribe(self, org_id: str) -> AsyncIterator[MissionStatusChanged]:
        q: asyncio.Queue[MissionStatusChanged] = asyncio.Queue()
        self._subs[org_id].add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[org_id].discard(q)


# Module-level singleton — imported by autopilot_routes.py.
_bus: LifecycleBus | None = None


def get_lifecycle_bus() -> LifecycleBus:
    """Return the process-global :class:`LifecycleBus`, creating it on first call."""
    global _bus
    if _bus is None:
        _bus = LifecycleBus()
    return _bus
