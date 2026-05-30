# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Subscription base-type tests."""
from __future__ import annotations

from sagewai.connections.subscriptions.base import (
    DrainResult,
    EmitResult,
    SubscriptionPlugin,
    SubscriptionStats,
)


def test_emit_result_values():
    assert EmitResult.ACCEPTED.value == "accepted"
    assert EmitResult.DROPPED_OVERSIZED.value == "dropped_oversized"
    assert EmitResult.DROPPED_OVERFLOW.value == "dropped_overflow"
    assert EmitResult.DROPPED_GLOBAL_PRESSURE.value == "dropped_global_pressure"
    assert EmitResult.BUFFER_FULL_PAUSE.value == "buffer_full_pause"


def test_drain_result_shape():
    dr = DrainResult(
        events=[{"x": 1}],
        returned=1,
        remaining=4,
        overflow_dropped=2,
        oversized_dropped=0,
        global_pressure_dropped=0,
    )
    assert dr.returned == 1
    assert dr.remaining == 4
    assert dr.overflow_dropped == 2


def test_subscription_stats_shape():
    st = SubscriptionStats(
        subscription_id="sub-1",
        connection_id="conn-1",
        status="active",
        buffer_depth=3,
        bytes_buffered=300,
        overflow_dropped=0,
        oversized_dropped=0,
        global_pressure_dropped=0,
        last_event_at=None,
        last_drain_at=123.0,
        created_at=100.0,
    )
    assert st.subscription_id == "sub-1"
    assert st.status == "active"


def test_subscription_plugin_is_runtime_checkable():
    """A class implementing the 3 methods passes isinstance."""

    class Impl:
        def subscription_spec_schema(self):
            ...

        async def open_subscription(self, connection, *, spec, emit, ctx):
            ...

        async def close_subscription(self, connection, *, spec):
            ...

    assert isinstance(Impl(), SubscriptionPlugin)


def test_non_impl_fails_runtime_check():
    class NotImpl:
        pass

    assert not isinstance(NotImpl(), SubscriptionPlugin)
