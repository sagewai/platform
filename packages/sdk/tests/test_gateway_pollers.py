# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
# packages/sagewai/tests/test_gateway_pollers.py
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock
from sagewai.gateway.pollers import Poller, PollerManager
from sagewai.gateway.triggers import IncomingEvent


class MockPoller(Poller):
    connector = "test"
    interval = timedelta(seconds=1)
    channels = ["#test"]

    async def poll(self) -> list[IncomingEvent]:
        return []

    def __init__(self):
        self.poll = AsyncMock(return_value=[])  # type: ignore[method-assign]


def test_poller_has_required_interface():
    p = MockPoller()
    assert p.connector == "test"
    assert p.interval == timedelta(seconds=1)


@pytest.mark.asyncio
async def test_poller_manager_register():
    mgr = PollerManager()
    p = MockPoller()
    handler = AsyncMock()
    mgr.register(p, handler)
    assert len(mgr._pollers) == 1
