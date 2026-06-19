# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""FleetReaper periodic loop: ticks, clean shutdown, survives a tick error."""
from __future__ import annotations

import asyncio

import pytest

from sagewai.fleet.reaper import FleetReaper


class _Store:
    def __init__(self, result=None, boom=False):
        self.calls = 0
        self._result = result or {"failed": 0, "requeued": 0}
        self._boom = boom

    async def reap_expired_leases(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError("boom")
        return self._result


@pytest.mark.asyncio
async def test_reaper_ticks_then_closes():
    store = _Store(result={"failed": 0, "requeued": 1})
    r = FleetReaper(store, interval_seconds=0.01)
    r.start()
    await asyncio.sleep(0.05)
    await r.aclose()
    assert store.calls >= 1


@pytest.mark.asyncio
async def test_reaper_survives_tick_error():
    store = _Store(boom=True)
    r = FleetReaper(store, interval_seconds=0.01)
    r.start()
    await asyncio.sleep(0.05)
    await r.aclose()
    assert store.calls >= 2  # kept ticking despite the error
