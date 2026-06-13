# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The global /workflow-events/stream SSE feed must stay OPEN.

A generator that ends after one event makes the browser's EventSource reconnect
in a tight loop, which floods the backend and exhausts the per-origin connection
limit (then the dashboard's health check times out → "Backend not reachable").
"""

import pytest

import sagewai.admin.serve as serve


@pytest.mark.asyncio
async def test_workflow_events_sse_stays_open(monkeypatch):
    monkeypatch.setattr(serve, "_WORKFLOW_EVENTS_HEARTBEAT_S", 0.001)
    gen = serve._workflow_events_sse()
    try:
        first = await gen.__anext__()
        assert first == "data: {}\n\n"  # initial event
        # The old stub yielded ONLY that one event and stopped (StopAsyncIteration
        # here) — the reconnect storm. The fix keeps the stream open with
        # heartbeat comments.
        assert (await gen.__anext__()) == ": keepalive\n\n"
        assert (await gen.__anext__()) == ": keepalive\n\n"
    finally:
        await gen.aclose()
