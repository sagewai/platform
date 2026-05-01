# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""CostOverrunSource — telemetry-driven cost overrun signal.

Reads a per-run cost ledger (CostTrackerView) and compares to the
run's estimated_cost_usd field. Emits when actual > estimated × multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sagewai.sealed.directives.models import SignalEvent
from sagewai.sealed.directives.signals import SignalContext


class CostTrackerView(Protocol):
    """Read-side handle for per-run cost data.

    Implementations live in the worker (in-memory aggregation of
    sagewai.observability.costs CostTracker events).
    """

    def get_run_cost_usd(self, run_id: str) -> float | None: ...


@dataclass
class CostOverrunSource:
    """Signal source — actual cost > estimated × multiplier."""

    tracker: CostTrackerView
    multiplier: float = 5.0
    name: str = "cost_overrun"

    async def collect(
        self,
        *,
        run,
        step_index: int,
        context: SignalContext,
    ) -> list[SignalEvent]:
        actual = self.tracker.get_run_cost_usd(run.run_id)
        estimated = run.estimated_cost_usd or 0.0
        if actual is None or estimated <= 0:
            return []
        threshold = estimated * self.multiplier
        if actual <= threshold:
            return []
        severity = "critical" if actual > estimated * 10 else "warning"
        return [
            SignalEvent(
                kind="cost_overrun",
                run_id=run.run_id,
                project_id=getattr(run, "project_id", None),
                workflow_name=run.workflow_name,
                step_index=step_index,
                severity=severity,
                detail=(
                    f"actual={actual:.2f} > {self.multiplier}× "
                    f"estimated={estimated:.2f}"
                ),
                evidence={
                    "actual_cost_usd": actual,
                    "estimated_cost_usd": estimated,
                    "multiplier": self.multiplier,
                    "threshold": threshold,
                },
                emitted_at=datetime.now(tz=timezone.utc),
            )
        ]
