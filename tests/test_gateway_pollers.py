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
