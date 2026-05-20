# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.notify."""
import logging

import pytest

from sagewai.tools.builtins import mission_state as ms
from sagewai.tools.builtins import notify as notify_mod


@pytest.mark.asyncio
async def test_notify_log_channel(caplog):
    caplog.set_level(logging.INFO, logger="sagewai.tools.notify")
    out = await notify_mod.notify({
        "channel": "log", "subject": "S", "body": "B", "mission_id": "m-1",
    })
    assert out == {"delivered": True, "channel": "log"}
    assert any(record.levelno == logging.INFO for record in caplog.records)


@pytest.mark.asyncio
async def test_notify_event_bus_channel():
    events: list[tuple[str, dict]] = []

    class FakeMission:
        def publish_event(self, topic, payload):
            events.append((topic, payload))

    ms.set_mission_resolver(lambda mid: FakeMission())
    try:
        out = await notify_mod.notify({
            "channel": "event_bus", "subject": "S", "body": "B", "mission_id": "m-1",
        })
        assert out == {"delivered": True, "channel": "event_bus"}
        assert events == [("notification.dispatched", {"subject": "S", "body": "B"})]
    finally:
        ms.set_mission_resolver(ms._default_resolver)


@pytest.mark.asyncio
async def test_notify_event_bus_without_mission_id_raises():
    with pytest.raises(ValueError, match="mission_id"):
        await notify_mod.notify({"channel": "event_bus", "subject": "S", "body": "B"})


@pytest.mark.asyncio
async def test_notify_unknown_channel_raises():
    with pytest.raises(ValueError, match="unknown channel"):
        await notify_mod.notify({"channel": "carrier-pigeon", "subject": "S", "body": "B"})
