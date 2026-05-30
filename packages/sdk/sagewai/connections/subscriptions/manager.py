# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SubscriptionManager — owns every subscriber task + buffer.

Core lifecycle (subscribe/drain/unsubscribe/stats/aclose) lives here.
Memory-safety bounds + reapers are added in the same file (Task 5).
"""
from __future__ import annotations

import asyncio
import collections
import logging
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from sagewai.connections.subscriptions.base import (
    DrainResult,
    EmitResult,
    SubscriptionPlugin,
    SubscriptionStats,
)
from sagewai.connections.subscriptions.errors import (
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)

logger = logging.getLogger(__name__)

# ── process-wide hard caps (per-subscription config may lower, never raise) ──
DEFAULT_MAX_EVENTS_PER_SUBSCRIPTION = 1000
DEFAULT_MAX_EVENT_BYTES = 256 * 1024            # 256 KiB
DEFAULT_MAX_TOTAL_BUFFERED_BYTES = 128 * 1024 * 1024   # 128 MiB
DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS = 64
DEFAULT_IDLE_TTL_SECONDS = 600.0                # 10 min
DEFAULT_MAX_RECONNECT_ATTEMPTS = 3


def _event_bytes(event: dict) -> int:
    """Approximate in-memory size of an event for the byte ceiling."""
    return sys.getsizeof(event) + sum(
        sys.getsizeof(k) + sys.getsizeof(v) for k, v in event.items()
    )


@dataclass
class ActiveSubscription:
    subscription_id: str
    connection_id: str
    connection: Any
    plugin: Any
    spec: dict
    buffer: "collections.deque[dict]"
    max_event_bytes: int
    bytes_buffered: int = 0
    task: asyncio.Task | None = None
    status: str = "active"           # active | reconnecting | failed
    created_at: float = 0.0
    last_event_at: float | None = None
    last_drain_at: float = 0.0
    overflow_dropped: int = 0
    oversized_dropped: int = 0
    global_pressure_dropped: int = 0
    reconnect_attempts: int = 0
    # The decrypt context (a PluginContext carrying ``creds``) captured at
    # subscribe time. Stored here so the dead-task reaper respawns the
    # subscriber with the SAME context as the first run — the single source
    # of truth for credential decryption on the streaming path (issue #378).
    ctx: Any = None


class SubscriptionManager:
    def __init__(
        self,
        *,
        time_source: Callable[[], float] = time.monotonic,
        max_events_per_subscription: int = DEFAULT_MAX_EVENTS_PER_SUBSCRIPTION,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_total_buffered_bytes: int = DEFAULT_MAX_TOTAL_BUFFERED_BYTES,
        max_active_subscriptions: int = DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._now = time_source
        self._max_events = max_events_per_subscription
        self._max_event_bytes = max_event_bytes
        self._max_total_bytes = max_total_buffered_bytes
        self._max_active = max_active_subscriptions
        self._idle_ttl = idle_ttl_seconds
        self._max_reconnect = max_reconnect_attempts
        self._subs: dict[str, ActiveSubscription] = {}
        self._total_bytes = 0
        self._reaper_task: asyncio.Task | None = None

    # ── public API ────────────────────────────────────────────────────

    async def subscribe(
        self,
        *,
        plugin: SubscriptionPlugin,
        connection: Any,   # sagewai.connections.models.Connection
        spec: dict,
        ctx: Any,          # PluginContext
    ) -> str:
        if len(self._subs) >= self._max_active:
            raise SubscriptionLimitExceededError(limit=self._max_active)
        # Validate spec via the plugin's schema BEFORE opening anything.
        plugin.subscription_spec_schema()(**spec)

        # Per-subscription bounds may LOWER the hard caps, never raise them.
        eff_max_events = self._clamp(spec.get("max_events_per_subscription"), self._max_events)
        eff_max_bytes = self._clamp(spec.get("max_event_bytes"), self._max_event_bytes)
        # Floor the ring depth at 1: a requested 0 → deque(maxlen=0) silently
        # black-holes every event; a negative → deque(maxlen=-1) raises a raw
        # ValueError. The ring always holds at least one event.
        eff_max_events = max(1, eff_max_events)

        sub_id = "sub-" + secrets.token_hex(8)
        sub = ActiveSubscription(
            subscription_id=sub_id,
            connection_id=getattr(connection, "id", "unknown"),
            connection=connection,
            plugin=plugin,
            spec=spec,
            buffer=collections.deque(maxlen=eff_max_events),
            max_event_bytes=eff_max_bytes,
            created_at=self._now(),
            last_drain_at=self._now(),
            ctx=ctx,
        )
        self._subs[sub_id] = sub
        sub.task = asyncio.ensure_future(self._run_subscriber(sub))
        return sub_id

    async def drain(self, subscription_id: str, max_events: int) -> DrainResult:
        sub = self._subs.get(subscription_id)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id=subscription_id)
        sub.last_drain_at = self._now()
        out: list[dict] = []
        for _ in range(max_events):
            if not sub.buffer:
                break
            event = sub.buffer.popleft()
            self._total_bytes -= _event_bytes(event)
            sub.bytes_buffered -= _event_bytes(event)
            out.append(event)
        return DrainResult(
            events=out,
            returned=len(out),
            remaining=len(sub.buffer),
            overflow_dropped=sub.overflow_dropped,
            oversized_dropped=sub.oversized_dropped,
            global_pressure_dropped=sub.global_pressure_dropped,
        )

    async def unsubscribe(self, subscription_id: str) -> None:
        sub = self._subs.pop(subscription_id, None)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id=subscription_id)
        await self._teardown(sub)

    def stats(self, subscription_id: str) -> SubscriptionStats:
        sub = self._subs.get(subscription_id)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id=subscription_id)
        return self._to_stats(sub)

    def list_subscriptions(self) -> list[SubscriptionStats]:
        return [self._to_stats(s) for s in self._subs.values()]

    async def aclose(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        for sub in list(self._subs.values()):
            await self._teardown(sub)
        self._subs.clear()
        self._total_bytes = 0

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _clamp(requested: int | None, hard_cap: int) -> int:
        if requested is None:
            return hard_cap
        return min(int(requested), hard_cap)

    def _to_stats(self, sub: ActiveSubscription) -> SubscriptionStats:
        return SubscriptionStats(
            subscription_id=sub.subscription_id,
            connection_id=sub.connection_id,
            status=sub.status,
            buffer_depth=len(sub.buffer),
            bytes_buffered=sub.bytes_buffered,
            overflow_dropped=sub.overflow_dropped,
            oversized_dropped=sub.oversized_dropped,
            global_pressure_dropped=sub.global_pressure_dropped,
            last_event_at=sub.last_event_at,
            last_drain_at=sub.last_drain_at,
            created_at=sub.created_at,
        )

    def _make_emit(self, sub: ActiveSubscription) -> Callable[[dict], EmitResult]:
        def emit(event: dict) -> EmitResult:
            size = _event_bytes(event)
            if size > sub.max_event_bytes:
                sub.oversized_dropped += 1
                return EmitResult.DROPPED_OVERSIZED

            # Will appending to a full drop_oldest ring evict its leftmost
            # event? ``deque(maxlen)`` auto-evicts ``buffer[0]`` on append.
            will_evict = (
                sub.buffer.maxlen is not None
                and len(sub.buffer) >= sub.buffer.maxlen
            )
            # The eviction frees ``evict_credit`` bytes, so the NET change to
            # the process-wide total is ``size - evict_credit`` — check the
            # global ceiling against that, not the gross ``size``. A gross
            # check makes a full ring go permanently stale under modest
            # global pressure even though accepting-and-evicting keeps the
            # total flat.
            evict_credit = _event_bytes(sub.buffer[0]) if will_evict else 0
            if self._total_bytes + size - evict_credit > self._max_total_bytes:
                sub.global_pressure_dropped += 1
                return EmitResult.DROPPED_GLOBAL_PRESSURE

            # drop_oldest: deque(maxlen) evicts oldest automatically on append.
            if will_evict:
                sub.overflow_dropped += 1
                self._total_bytes -= evict_credit
                sub.bytes_buffered -= evict_credit
                sub.buffer.append(event)
                result = EmitResult.DROPPED_OVERFLOW
            else:
                sub.buffer.append(event)
                result = EmitResult.ACCEPTED

            sub.last_event_at = self._now()
            self._total_bytes += size
            sub.bytes_buffered += size
            return result

        return emit

    async def _run_subscriber(self, sub: ActiveSubscription) -> None:
        """Background task: drive the plugin's open_subscription with the
        manager-owned emit. A crash (any non-cancellation exception) flips
        the subscription to ``reconnecting`` and returns; the dead-task
        reaper handles the bounded restart-or-fail.

        The decrypt context is read from ``sub.ctx`` (captured at subscribe
        time) so the first run AND every reaper respawn use the same
        credential context (issue #378)."""
        emit = self._make_emit(sub)
        try:
            await sub.plugin.open_subscription(
                sub.connection, spec=sub.spec, emit=emit, ctx=sub.ctx
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # subscriber crashed — dead-task reaper handles it
            sub.status = "reconnecting"
            logger.info(
                "subscription subscriber task exited",
                extra={
                    "event": "subscription.subscriber_crashed",
                    "subscription_id": sub.subscription_id,
                    "connection_id": sub.connection_id,
                    "error": str(exc),
                },
            )

    async def _reap_idle_once(self) -> None:
        """Auto-unsubscribe subscriptions with no drain in ``idle_ttl``."""
        # "Idle" means no CONSUMER DRAIN in ``idle_ttl`` (keyed off
        # ``last_drain_at``), NOT no upstream activity. A live-but-undrained
        # stream is intentionally reaped — nothing is consuming it, so there
        # is no consumer interest to keep it (and its buffer) alive.
        # ``last_event_at`` is observability-only; it does not spare a sub.
        now = self._now()
        stale = [
            s for s in self._subs.values()
            if now - s.last_drain_at > self._idle_ttl
        ]
        for sub in stale:
            self._subs.pop(sub.subscription_id, None)
            await self._teardown(sub)
            logger.info(
                "subscription idle-reaped",
                extra={
                    "event": "subscription.idle_reaped",
                    "subscription_id": sub.subscription_id,
                    "connection_id": sub.connection_id,
                    "idle_seconds": now - sub.last_drain_at,
                },
            )

    async def _reap_dead_once(self) -> None:
        """Restart crashed subscriber tasks (bounded); mark ``failed`` on
        exhaustion. Respawns read ``sub.ctx`` — the same decrypt context the
        first run used (issue #378)."""
        for sub in list(self._subs.values()):
            if sub.task is None or not sub.task.done():
                continue
            # Task finished. If it raised (not cancelled), attempt reconnect.
            if sub.status == "failed":
                continue
            if sub.reconnect_attempts >= self._max_reconnect:
                sub.status = "failed"
                logger.info(
                    "subscription failed after reconnect exhaustion",
                    extra={
                        "event": "subscription.failed",
                        "subscription_id": sub.subscription_id,
                        "connection_id": sub.connection_id,
                    },
                )
                continue
            sub.reconnect_attempts += 1
            sub.status = "reconnecting"
            sub.task = asyncio.ensure_future(self._run_subscriber(sub))

    async def _reaper_loop(self, interval_seconds: float = 60.0) -> None:
        """Periodic reaper. Started by ``start_reaper``, cancelled by aclose.

        Scheduling is intentionally separate from reaper logic: the
        ``_reap_idle_once`` / ``_reap_dead_once`` once-methods are unit-tested
        directly with an injectable clock; this loop is only the 60s cadence.
        """
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self._reap_idle_once()
                await self._reap_dead_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover — never let the loop die
                logger.exception("subscription reaper tick failed")

    def start_reaper(self, *, interval_seconds: float = 60.0) -> None:
        """Start the periodic reaper loop (called by admin lifespan).

        No ``ctx`` param: each subscription carries its own decrypt context
        (``ActiveSubscription.ctx``), so respawns need nothing from here
        (issue #378, Approach A)."""
        if self._reaper_task is None:
            self._reaper_task = asyncio.ensure_future(
                self._reaper_loop(interval_seconds=interval_seconds)
            )

    async def _teardown(self, sub: ActiveSubscription) -> None:
        if sub.task is not None and not sub.task.done():
            sub.task.cancel()
            try:
                await sub.task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await sub.plugin.close_subscription(sub.connection, spec=sub.spec)
        except Exception:  # best-effort teardown — log but never fatal
            logger.warning(
                "subscription close_subscription failed during teardown",
                exc_info=True,
            )
        self._total_bytes -= sub.bytes_buffered
        sub.bytes_buffered = 0
        sub.buffer.clear()


# ── process-wide singleton (admin lifespan sets it; executor + routes read it) ──

_MANAGER: SubscriptionManager | None = None


def set_subscription_manager(mgr: SubscriptionManager | None) -> None:
    """Set the process-wide manager. Admin lifespan sets it at startup;
    tests set/clear it; pass None to clear."""
    global _MANAGER
    _MANAGER = mgr


def get_subscription_manager() -> SubscriptionManager:
    """Return the process-wide manager, or raise if not initialized."""
    if _MANAGER is None:
        raise RuntimeError(
            "SubscriptionManager not initialized; admin lifespan sets it at startup"
        )
    return _MANAGER
