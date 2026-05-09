# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-process per-mission asyncio bus with replay + ring buffer + multi-subscriber fan-out.

:class:`MissionRunBus` backs two autopilot endpoints:

* ``POST /missions/{id}/run`` — the run driver publishes events via
  :meth:`MissionRunBus.publish`.
* ``GET /missions/{id}/events`` — each SSE consumer calls
  :meth:`MissionRunBus.subscribe` once per request; the returned
  :class:`asyncio.Queue` receives every subsequent event **plus** a
  replay of the current ring buffer so reload-mid-run works without
  reconstructing from disk.

Ring buffer semantics
---------------------
* Each ``mission_id`` has its own ``deque`` capped at ``ring_max``
  (default 1000).  When the cap is reached the *oldest* event is
  evicted (``deque.maxlen`` handles this automatically).
* On :meth:`publish`: append to the ring buffer *first*, then
  ``put_nowait`` onto all current subscriber queues.
* On :meth:`subscribe`: replay the entire current ring buffer into the
  new queue via ``put_nowait`` before returning it.

Subclassing
-----------
:meth:`publish` is designed to be overridden.  Plans I/J/K (fleet /
sandbox / sealed wrappers) can subclass :class:`MissionRunBus` and
chain side-effects inside ``publish`` after calling ``super().publish()``.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any


class MissionRunBus:
    """Per-mission run-event bus with ring-buffer replay and fan-out delivery."""

    def __init__(self, *, ring_max: int = 1000) -> None:
        self._ring_max = ring_max
        # ring_max=0 is valid (no replay); deque(maxlen=0) silently drops everything.
        self._rings: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._ring_max)
        )
        self._subs: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    def subscribe(self, mission_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Create a new subscriber queue and replay the current ring buffer into it.

        The queue is pre-populated with all events currently in the ring
        buffer so a late subscriber receives prior events immediately on
        the first :meth:`~asyncio.Queue.get` call.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Replay prior events into the queue before registering, so there
        # is no window where the subscriber misses a concurrent publish.
        for event in self._rings[mission_id]:
            q.put_nowait(event)
        self._subs[mission_id].append(q)
        return q

    def unsubscribe(self, mission_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove *q* from the subscriber list for *mission_id*.

        Idempotent — silently does nothing if *q* is not registered.
        """
        try:
            self._subs[mission_id].remove(q)
        except ValueError:
            pass

    async def publish(self, mission_id: str, event: dict[str, Any]) -> None:
        """Append *event* to the ring buffer then deliver it to all subscribers.

        The ring buffer is updated *before* fan-out so that a subscriber
        calling :meth:`subscribe` immediately after this coroutine returns
        will see the event in its replay.

        Subclasses may override this method to add side-effects; call
        ``await super().publish(mission_id, event)`` to preserve base
        behaviour.
        """
        self._rings[mission_id].append(event)
        for q in list(self._subs[mission_id]):
            q.put_nowait(event)


_BUS_SINGLETON: MissionRunBus | None = None


def get_run_bus() -> MissionRunBus:
    """Return the process-global :class:`MissionRunBus`, creating it on first call.

    Mirrors :func:`~sagewai.admin.autopilot_lifecycle_bus.get_lifecycle_bus`.
    """
    global _BUS_SINGLETON
    if _BUS_SINGLETON is None:
        _BUS_SINGLETON = MissionRunBus()
    return _BUS_SINGLETON
