# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Subscription foundation base types.

``SubscriptionPlugin`` is the optional second Protocol a connection
plugin implements (alongside ``ProtocolPlugin``) to gain streaming.
The plugin only knows how to connect-and-emit; the ``SubscriptionManager``
owns the buffer, the bounds, and the lifecycle.
"""
from __future__ import annotations

import enum
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class EmitResult(str, enum.Enum):
    """Return value of the manager-owned ``emit`` callback the plugin calls.

    A ``drop_oldest`` plugin ignores the result (the ring handles eviction).
    PR1's manager only does ``drop_oldest``. The ``pause`` overflow policy —
    where a plugin stops consuming on ``BUFFER_FULL_PAUSE`` and resumes when
    the manager signals space — is a PR2 (MQTT) feature: it requires a real
    async consumer to validate the resume semantics. The enum value is kept
    here as the forward contract PR2 builds on.
    """

    ACCEPTED = "accepted"
    DROPPED_OVERSIZED = "dropped_oversized"
    DROPPED_OVERFLOW = "dropped_overflow"
    DROPPED_GLOBAL_PRESSURE = "dropped_global_pressure"
    # Returned by the manager's emit only in ``pause`` overflow mode —
    # implemented in PR2 (MQTT) where a real async consumer validates the
    # resume signal. PR1's manager uses ``drop_oldest`` only.
    BUFFER_FULL_PAUSE = "buffer_full_pause"


class DrainResult(BaseModel):
    """Returned by ``SubscriptionManager.drain``. Tells the agent whether
    it is seeing the complete stream or a lossy sample."""

    model_config = ConfigDict(extra="forbid")

    events: list[dict[str, Any]]
    returned: int
    remaining: int
    overflow_dropped: int
    oversized_dropped: int
    global_pressure_dropped: int


class SubscriptionStats(BaseModel):
    """Observability snapshot for one active subscription."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: str
    connection_id: str
    status: Literal["active", "reconnecting", "failed"]
    buffer_depth: int
    bytes_buffered: int
    overflow_dropped: int
    oversized_dropped: int
    global_pressure_dropped: int
    last_event_at: float | None
    last_drain_at: float
    created_at: float


@runtime_checkable
class SubscriptionPlugin(Protocol):
    """Optional second Protocol for streaming-capable connection plugins."""

    def subscription_spec_schema(self) -> type[BaseModel]:
        """Pydantic model validating the per-subscription ``spec``."""

    async def open_subscription(
        self,
        connection: Any,            # sagewai.connections.models.Connection
        *,
        spec: dict[str, Any],
        emit: Callable[[dict[str, Any]], EmitResult],
        ctx: Any,                   # PluginContext
    ) -> None:
        """Connect to the source, consume forever, call ``emit(event)`` per
        message. Runs as the manager's background task. Does NOT manage the
        buffer. Raises typed errors on connect/auth failure; the manager's
        dead-task reaper handles crashes."""

    async def close_subscription(
        self, connection: Any, *, spec: dict[str, Any]
    ) -> None:
        """Tear down the source connection + stop consuming."""
