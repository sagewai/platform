# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Background recovery worker for stale workflows.

Periodically scans for RUNNING workflows that haven't heartbeated
and resets them to PENDING so the WorkflowWorker re-claims and
resumes from the last checkpoint.

Two modes:
1. **Batch reset** (preferred): Uses ``PostgresStore.reset_stale_to_pending()``
   to atomically reset all stale runs in a single UPDATE.
2. **Per-run handler** (legacy): Calls a handler per stale run for
   backward compatibility.

Usage::

    from sagewai.core.recovery import RecoveryWorker

    # Preferred: batch reset (no handler needed)
    worker = RecoveryWorker(store=postgres_store)
    asyncio.create_task(worker.start())

    # Legacy: per-run handler
    worker = RecoveryWorker(store=postgres_store, handler=my_handler)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sagewai.core.state import WorkflowRun, WorkflowStore

logger = logging.getLogger(__name__)


class RecoveryWorker:
    """Background worker that recovers stale workflows.

    If no ``handler`` is provided and the store has ``reset_stale_to_pending``,
    uses batch reset mode — all stale RUNNING runs are atomically set to
    PENDING in one UPDATE. The WorkflowWorker then re-claims them and
    DurableRunner resumes from the last checkpoint.

    Parameters
    ----------
    store:
        WorkflowStore to scan for stale runs.
    handler:
        Optional async callback invoked per stale run.
        If None and store supports batch reset, uses that instead.
    interval:
        Seconds between scans (default: 60).
    stale_timeout:
        Seconds after which a RUNNING workflow is considered stale (default: 300).
    """

    def __init__(
        self,
        store: WorkflowStore,
        handler: Callable[[WorkflowRun], Awaitable[None]] | None = None,
        interval: float = 60.0,
        stale_timeout: int = 300,
    ) -> None:
        self._store = store
        self._handler = handler
        self._interval = interval
        self._stale_timeout = stale_timeout
        self._running = False
        # Check if store supports batch reset
        self._use_batch_reset = (
            handler is None and hasattr(store, "reset_stale_to_pending")
        )

    async def start(self) -> None:
        """Run the recovery loop until stop() is called."""
        self._running = True
        mode = "batch-reset" if self._use_batch_reset else "per-run-handler"
        logger.info(
            "Recovery worker started (mode=%s, interval=%.0fs, stale_timeout=%ds)",
            mode,
            self._interval,
            self._stale_timeout,
        )
        while self._running:
            try:
                if self._use_batch_reset:
                    count = await self._store.reset_stale_to_pending(self._stale_timeout)
                    if count > 0:
                        logger.info(
                            "Recovery: reset %d stale runs to PENDING", count
                        )
                else:
                    stale = await self._store.recover_stale_runs(self._stale_timeout)
                    for run in stale:
                        logger.info(
                            "Recovering stale workflow: %s run %s",
                            run.workflow_name,
                            run.run_id,
                        )
                        try:
                            await self._handler(run)
                        except Exception:  # noqa: broad-exception-caught — resilience
                            logger.exception(
                                "Recovery handler failed for %s:%s",
                                run.workflow_name,
                                run.run_id,
                            )
            except Exception:  # noqa: broad-exception-caught — scan loop resilience
                logger.exception("Recovery worker scan failed")

            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        """Signal the worker to stop after the current scan."""
        self._running = False
        logger.info("Recovery worker stopping")
