# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkflowSupervisor — background stale detection and recovery.

Periodically checks for stale RUNNING workflows (those that haven't
sent a heartbeat within the timeout) and resets them to PENDING for
re-claim by a WorkflowWorker.

Can run alongside a WorkflowWorker or as a standalone process.

Usage::

    from sagewai.core.supervisor import WorkflowSupervisor
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    await store.initialize()

    supervisor = WorkflowSupervisor(store=store)
    await supervisor.start()  # blocks until stopped
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowSupervisor:
    """Background supervisor for workflow health monitoring.

    Parameters
    ----------
    store:
        WorkflowStore with stale detection capabilities.
    check_interval:
        Seconds between health checks (default: 60).
    stale_timeout:
        Seconds after which a RUNNING workflow without heartbeat
        is considered stale (default: 300 = 5 minutes).
    on_stale_detected:
        Optional async callback called when stale runs are found.
        Receives list of reset run_ids.
    """

    def __init__(
        self,
        store: Any,
        *,
        check_interval: float = 60.0,
        stale_timeout: int = 300,
        on_stale_detected: Any = None,
    ) -> None:
        self._store = store
        self._check_interval = check_interval
        self._stale_timeout = stale_timeout
        self._on_stale_detected = on_stale_detected
        self._shutdown = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        """Run the supervisor loop. Blocks until stop() is called."""
        self._running = True
        self._shutdown.clear()
        logger.info(
            "Supervisor started (check_interval=%.0fs, stale_timeout=%ds)",
            self._check_interval,
            self._stale_timeout,
        )

        while not self._shutdown.is_set():
            try:
                await self._check_health()
            except Exception:
                logger.exception("Supervisor health check failed")

            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self._check_interval,
                )
                break  # shutdown was signaled
            except asyncio.TimeoutError:
                continue  # normal timeout, loop again

        self._running = False
        logger.info("Supervisor stopped")

    async def stop(self) -> None:
        """Signal the supervisor to stop."""
        self._shutdown.set()

    @property
    def is_running(self) -> bool:
        """Whether the supervisor is currently running."""
        return self._running

    async def _check_health(self) -> None:
        """Run one health check cycle."""
        # Reset stale runs
        if hasattr(self._store, "reset_stale_to_pending"):
            count = await self._store.reset_stale_to_pending(
                self._stale_timeout
            )
            if count > 0:
                logger.warning(
                    "Supervisor reset %d stale runs to PENDING", count
                )
                if self._on_stale_detected:
                    try:
                        await self._on_stale_detected(count)
                    except Exception:
                        logger.exception(
                            "on_stale_detected callback failed"
                        )

        # Log queue stats if available
        if hasattr(self._store, "count_by_status"):
            try:
                stats = await self._store.count_by_status()
                pending = stats.get("pending", 0)
                running = stats.get("running", 0)
                failed = stats.get("failed", 0)
                if pending > 0 or running > 0 or failed > 0:
                    logger.info(
                        "Queue stats: pending=%d running=%d failed=%d",
                        pending,
                        running,
                        failed,
                    )
            except Exception:
                pass

    async def run_once(self) -> None:
        """Run a single health check (useful for testing/cron)."""
        await self._check_health()
