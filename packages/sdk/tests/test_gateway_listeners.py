# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
# packages/sagewai/tests/test_gateway_listeners.py
import pytest
from unittest.mock import AsyncMock
from sagewai.gateway.listeners import Listener, ListenerManager
from sagewai.gateway.triggers import IncomingEvent


class MockListener(Listener):
    connector = "test"
    channels = ["#test"]

    def __init__(self):
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stopped = True


def test_listener_has_required_interface():
    listener = MockListener()
    assert listener.connector == "test"


@pytest.mark.asyncio
async def test_listener_manager_lifecycle():
    mgr = ListenerManager()
    listener = MockListener()
    mgr.register(listener)
    await mgr.start_all()
    assert listener._started is True
    await mgr.stop_all()
    assert listener._stopped is True
