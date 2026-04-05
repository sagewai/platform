# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""RunController — pause, resume, and cancel controls for running agents.

Provides per-run control signals that integrate with execution strategies
via checkpoint calls in the iteration loop.
"""

from __future__ import annotations

import asyncio


class AgentCancelledError(Exception):
    """Raised when an agent run is cancelled via admin controls."""


class RunController:
    """Controls a single running agent invocation.

    The execution strategy calls :meth:`checkpoint` at the start of each
    iteration. If the run is paused, the checkpoint blocks until resumed.
    If cancelled, it raises :class:`AgentCancelledError`.
    """

    def __init__(self) -> None:
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # not paused by default
        self._cancelled = False
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    async def checkpoint(self) -> None:
        """Check for pause/cancel signals. Call at each iteration boundary.

        Raises:
            AgentCancelledError: If the run has been cancelled.
        """
        if self._cancelled:
            raise AgentCancelledError("Run cancelled by admin")
        await self._pause_event.wait()
        # Re-check after unpausing in case cancel was set while paused
        if self._cancelled:
            raise AgentCancelledError("Run cancelled by admin")

    def pause(self) -> None:
        """Pause the run. The next checkpoint call will block."""
        self._paused = True
        self._pause_event.clear()

    def resume(self) -> None:
        """Resume a paused run."""
        self._paused = False
        self._pause_event.set()

    def cancel(self) -> None:
        """Cancel the run. The next checkpoint call will raise."""
        self._cancelled = True
        self._paused = False
        self._pause_event.set()  # unblock if paused so cancellation propagates


class RunControlRegistry:
    """Registry of active RunControllers keyed by run_id.

    Used by the admin API to look up controllers for live runs.
    """

    def __init__(self) -> None:
        self._controllers: dict[str, RunController] = {}

    def register(self, run_id: str, controller: RunController) -> None:
        """Register a controller for an active run."""
        self._controllers[run_id] = controller

    def get(self, run_id: str) -> RunController | None:
        """Get the controller for a run, or None if not active."""
        return self._controllers.get(run_id)

    def unregister(self, run_id: str) -> None:
        """Remove a controller when a run completes."""
        self._controllers.pop(run_id, None)

    def list_active(self) -> list[str]:
        """List all active run IDs."""
        return list(self._controllers.keys())
