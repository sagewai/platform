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
