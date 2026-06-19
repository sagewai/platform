# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Periodic reaper that requeues fleet tasks whose worker died (lease expired).

Mirrors sagewai.connections.subscriptions.manager.SubscriptionManager's reaper
shape: the reaping logic (PostgresTaskStore.reap_expired_leases) is a directly
unit-tested store method; this is only the cadence loop + lifecycle, started by
the admin lifespan and cancelled on shutdown.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class FleetReaper:
    def __init__(self, store, *, interval_seconds: float = 30.0) -> None:
        self._store = store
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def _reaper_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                res = await self._store.reap_expired_leases()
                if res["requeued"] or res["failed"]:
                    logger.info(
                        "fleet reaper tick",
                        extra={"event": "fleet.reaper.tick",
                               "requeued": res["requeued"], "failed": res["failed"]},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover — never let the loop die
                logger.exception("fleet reaper tick failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._reaper_loop())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
