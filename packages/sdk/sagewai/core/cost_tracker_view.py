# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Worker-side per-run cost ledger.

Accumulates LLM call costs into a per-run-id total. Hooked into the
existing CostTracker event stream; consumed by CostOverrunSource.
"""
from __future__ import annotations

import threading
from collections import defaultdict


class WorkerCostTrackerView:
    """Thread-safe per-run cost accumulator."""

    def __init__(self) -> None:
        self._totals: dict[str, float] = defaultdict(float)
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def record_llm_call(self, *, run_id: str, cost_usd: float) -> None:
        with self._lock:
            self._totals[run_id] += float(cost_usd)
            self._seen.add(run_id)

    def get_run_cost_usd(self, run_id: str) -> float | None:
        with self._lock:
            if run_id not in self._seen:
                return None
            return self._totals[run_id]

    def clear_run(self, run_id: str) -> None:
        with self._lock:
            self._totals.pop(run_id, None)
            self._seen.discard(run_id)
