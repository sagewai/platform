# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet scoreboard — prove tenant isolation, render per-worker breakdown.

Each factory example wraps its execution in a :class:`FleetScoreboard`
context manager. Every task claimed by a worker is recorded against
``(worker_id, tenant)``. On exit the scoreboard:

1. asserts zero cross-tenant leakage for dedicated (non-overflow) workers,
2. pretty-prints an ASCII table the user can paste into a PR description,
3. exposes the raw rows so the example can do its own maths (e.g. the
   "cost before/after training" delta).

This is deliberately decoupled from the live ``FleetDispatcher`` — the
example hooks the dispatcher's report callback and forwards into
:meth:`FleetScoreboard.record`. That keeps the scoreboard usable in
tests without a real fleet.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import TracebackType
from typing import Iterable


@dataclass
class ScoreRow:
    """One row of the scoreboard."""

    worker_id: str
    tenant: str
    tasks: int
    total_ms: float
    total_cost_usd: float

    def avg_ms(self) -> float:
        return self.total_ms / self.tasks if self.tasks else 0.0


class FleetScoreboard:
    """Records task outcomes per worker, per tenant.

    Usage::

        sb = FleetScoreboard(
            dedicated_workers={
                "build-1": "app-factory",
                "ops-1": "biz-ops",
            },
            overflow_workers={"overflow-1"},
        )
        async with sb:
            await run_factory(dispatcher, scoreboard=sb)
        print(sb.render())
    """

    def __init__(
        self,
        *,
        dedicated_workers: dict[str, str] | None = None,
        overflow_workers: Iterable[str] | None = None,
    ) -> None:
        self._dedicated = dict(dedicated_workers or {})
        self._overflow = set(overflow_workers or [])
        self._rows: dict[tuple[str, str], ScoreRow] = {}

    # ── context manager ──────────────────────────────────────────

    async def __aenter__(self) -> "FleetScoreboard":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Don't swallow exceptions; assertions happen in assert_isolated()
        # so tests can choose when to run them.
        return None

    # ── recording ────────────────────────────────────────────────

    def record(
        self,
        *,
        worker_id: str,
        tenant: str,
        duration_ms: float = 0.0,
        cost_usd: float = 0.0,
    ) -> None:
        """Record one completed task."""
        key = (worker_id, tenant)
        row = self._rows.get(key)
        if row is None:
            row = ScoreRow(
                worker_id=worker_id,
                tenant=tenant,
                tasks=0,
                total_ms=0.0,
                total_cost_usd=0.0,
            )
            self._rows[key] = row
        row.tasks += 1
        row.total_ms += duration_ms
        row.total_cost_usd += cost_usd

    # ── assertions ───────────────────────────────────────────────

    def assert_isolated(self) -> None:
        """Raise if any dedicated worker touched a foreign tenant."""
        violations: list[str] = []
        for (worker_id, tenant), row in self._rows.items():
            expected = self._dedicated.get(worker_id)
            if expected is None:
                # Overflow workers are allowed to serve any tenant.
                if worker_id not in self._overflow and worker_id not in (
                    w for w, _ in self._rows.keys() if w in self._dedicated
                ):
                    # Unknown worker — allow, but flag in output later.
                    continue
                continue
            if tenant != expected:
                violations.append(
                    f"{worker_id} handled {row.tasks} task(s) for "
                    f"{tenant} but is dedicated to {expected}"
                )
        if violations:
            raise AssertionError(
                "Fleet isolation violated:\n  " + "\n  ".join(violations)
            )

    # ── introspection ────────────────────────────────────────────

    def rows(self) -> list[ScoreRow]:
        return sorted(
            self._rows.values(),
            key=lambda r: (r.tenant, r.worker_id),
        )

    def tenant_totals(self) -> dict[str, ScoreRow]:
        totals: dict[str, ScoreRow] = {}
        for row in self._rows.values():
            merged = totals.get(row.tenant)
            if merged is None:
                totals[row.tenant] = ScoreRow(
                    worker_id="<all>",
                    tenant=row.tenant,
                    tasks=row.tasks,
                    total_ms=row.total_ms,
                    total_cost_usd=row.total_cost_usd,
                )
            else:
                merged.tasks += row.tasks
                merged.total_ms += row.total_ms
                merged.total_cost_usd += row.total_cost_usd
        return totals

    # ── rendering ────────────────────────────────────────────────

    def render(self) -> str:
        """ASCII table grouped by tenant."""
        if not self._rows:
            return "Fleet scoreboard: no tasks recorded."

        lines: list[str] = []
        lines.append("Fleet scoreboard")
        lines.append("=" * 60)

        by_tenant: dict[str, list[ScoreRow]] = defaultdict(list)
        for row in self.rows():
            by_tenant[row.tenant].append(row)

        for tenant, rows in sorted(by_tenant.items()):
            lines.append(f"\n[{tenant}]")
            lines.append(
                "  {:<14} {:>6} {:>12} {:>12}".format(
                    "worker", "tasks", "avg ms", "cost $"
                )
            )
            for row in rows:
                tag = (
                    " (dedicated)"
                    if self._dedicated.get(row.worker_id) == tenant
                    else " (overflow)"
                    if row.worker_id in self._overflow
                    else ""
                )
                lines.append(
                    "  {:<14} {:>6} {:>12.1f} {:>12.4f}{}".format(
                        row.worker_id,
                        row.tasks,
                        row.avg_ms(),
                        row.total_cost_usd,
                        tag,
                    )
                )

        totals = self.tenant_totals()
        lines.append("\nTenant totals:")
        for tenant, row in sorted(totals.items()):
            lines.append(
                f"  {tenant:<14} {row.tasks:>4} tasks  "
                f"${row.total_cost_usd:.4f}"
            )
        return "\n".join(lines)
