# packages/sagewai/tests/test_gateway_triggers.py
import pytest
from sagewai.gateway.triggers import (
    Strategy, EventFilter, TriggerSpec, IncomingEvent,
    TriggerManager, InMemoryTriggerStore,
)


def test_trigger_spec_creation():
    ts = TriggerSpec(
        source="slack", strategy=Strategy.WEBHOOK,
        filter=EventFilter(channels=["#support"]),
        target="support-agent", action="chat",
    )
    assert ts.enabled is True
    assert ts.context == {}


def test_event_filter_matches_channel():
    f = EventFilter(channels=["#support", "#help"])
    event = IncomingEvent(
        source="slack", event_type="message",
        channel="#support", payload={"text": "hi"}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is True


def test_event_filter_rejects_wrong_channel():
    f = EventFilter(channels=["#support"])
    event = IncomingEvent(
        source="slack", event_type="message",
        channel="#random", payload={}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is False


def test_event_filter_empty_matches_all():
    f = EventFilter()
    event = IncomingEvent(
        source="slack", event_type="message",
        payload={}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is True


def test_event_filter_matches_event_type():
    f = EventFilter(event_types=["order.created"])
    event = IncomingEvent(
        source="shopify", event_type="order.created",
        payload={}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is True


@pytest.mark.asyncio
async def test_trigger_store_crud():
    store = InMemoryTriggerStore()
    ts = TriggerSpec(
        source="slack", strategy=Strategy.POLLER,
        filter=EventFilter(), target="agent", action="chat",
    )
    await store.save("t1", ts)
    assert (await store.get("t1")) is not None
    items = await store.list_all()
    assert len(items) == 1
    await store.delete("t1")
    assert (await store.get("t1")) is None


from sagewai.gateway.triggers import AgentResolver


@pytest.mark.asyncio
async def test_trigger_manager_dispatch():
    from unittest.mock import AsyncMock, MagicMock

    mock_agent = MagicMock()
    mock_agent.chat = AsyncMock(return_value="OK")

    class MockResolver(AgentResolver):
        async def resolve(self, target):
            return mock_agent

    mgr = TriggerManager(agent_resolver=MockResolver())
    tid = await mgr.register(TriggerSpec(
        source="slack", strategy=Strategy.WEBHOOK,
        filter=EventFilter(channels=["#support"]),
        target="support-agent", action="chat",
    ))

    event = IncomingEvent(
        source="slack", event_type="message",
        channel="#support", payload={"text": "help me"},
        timestamp="2026-01-01T00:00:00Z",
    )
    await mgr.dispatch(event)
    mock_agent.chat.assert_called_once_with("help me")


def test_event_filter_keywords():
    f = EventFilter(keywords=["urgent", "help"])
    event = IncomingEvent(
        source="slack", event_type="message",
        payload={"text": "I need URGENT help"}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is True

    event2 = IncomingEvent(
        source="slack", event_type="message",
        payload={"text": "just saying hi"}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event2) is False


def test_event_filter_to():
    f = EventFilter(to=["support@company.com"])
    event = IncomingEvent(
        source="email", event_type="received",
        payload={"to": "support@company.com"}, timestamp="2026-01-01T00:00:00Z",
    )
    assert f.matches(event) is True
