# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared fixtures for subscription-manager tests.

``FakeSubscriptionPlugin`` is a synthetic ``SubscriptionPlugin`` that lets
a test push events on demand via a queue, so the entire manager + safety
machinery is exercised without any real protocol or broker.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict


class _FakeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "fake"
    overflow_policy: str = "drop_oldest"
    max_events_per_subscription: int | None = None
    max_event_bytes: int | None = None


class FakeSubscriptionPlugin:
    """Synthetic plugin. A test drives it by putting events on ``feed``.

    ``open_subscription`` loops reading from ``self.feed`` and calling
    ``emit``. Crash controls (for the dead-task reaper):

    - ``crash_on_open``: when set, every ``open_subscription`` raises
      *immediately* on entry — before awaiting the feed. This makes each
      respawned task crash deterministically, so the bounded-reconnect
      counter advances on every ``_reap_dead_once`` tick with no
      dependency on test-side feed timing.
    - ``crash_after``: when set, the loop raises after N emits. Useful for
      the first crash; the empty-feed block on respawn makes it
      non-deterministic on its own, hence ``crash_on_open`` for the
      exhaustion path.
    """

    def __init__(self) -> None:
        self.feed: asyncio.Queue = asyncio.Queue()
        self.opened = 0
        self.closed = 0
        self.crash_after: int | None = None
        self.crash_on_open: bool = False

    def subscription_spec_schema(self) -> type[BaseModel]:
        return _FakeSpec

    async def open_subscription(self, connection, *, spec, emit, ctx) -> None:
        self.opened += 1
        if self.crash_on_open:
            raise RuntimeError("synthetic subscriber crash on open")
        emitted = 0
        while True:
            event = await self.feed.get()
            emit(event)
            emitted += 1
            if self.crash_after is not None and emitted >= self.crash_after:
                raise RuntimeError("synthetic subscriber crash")

    async def close_subscription(self, connection, *, spec) -> None:
        self.closed += 1


@pytest.fixture
def fake_plugin():
    return FakeSubscriptionPlugin()


class FakeClock:
    """Monotonic-shaped injectable clock. Tests advance it manually."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def fake_connection():
    """Minimal Connection-shaped object for the manager (it only reads .id)."""
    from datetime import datetime, timezone

    from sagewai.connections.models import Connection

    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-fake",
        protocol="fake",
        project_id="proj",
        display_name="fake",
        tags=(),
        credentials_backend={"kind": "local"},
        status="ready",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=now,
        updated_at=now,
        last_error=None,
        protocol_data={},
    )
