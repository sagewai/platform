# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Signal-source framework — Protocol, registry, SignalCollector.

See spec §3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sagewai.sealed.directives.models import SignalEvent

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# SignalContext — read-side handles passed to every collect call
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SignalContext:
    """Read-side handles passed to every SignalSource.collect call.

    Source-specific extra reads should be passed via the source
    constructor, not added here. Keep this lean.
    """

    cost_tracker: Any | None = None  # CostTrackerView when available (Task 16)
    audit_reader: Any | None = None  # AuditReader (Sealed-i) for rotation drift
    store: Any | None = None  # WorkflowRun store for capability gap


# ──────────────────────────────────────────────────────────────────────
# SignalSource Protocol
# ──────────────────────────────────────────────────────────────────────


@runtime_checkable
class SignalSource(Protocol):
    """A source of evidence about a running workflow."""

    name: str

    async def collect(
        self,
        *,
        run: Any,
        step_index: int,
        context: SignalContext,
    ) -> list[SignalEvent]:
        ...


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────


_SIGNAL_SOURCES: dict[str, SignalSource] = {}


def register_signal_source(source: SignalSource) -> None:
    """Register a SignalSource. Called at import time by built-in sources;
    callable from external code for plugin sources."""
    _SIGNAL_SOURCES[source.name] = source


def list_signal_sources() -> list[SignalSource]:
    return list(_SIGNAL_SOURCES.values())


def clear_signal_sources_for_test() -> None:
    """Test helper — never call from production paths."""
    _SIGNAL_SOURCES.clear()


# ──────────────────────────────────────────────────────────────────────
# SignalCollector
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SignalCollector:
    """Runs all registered sources against a run and aggregates signals.

    Catches per-source exceptions so one broken source can't take down
    directive evaluation for the entire fleet.
    """

    sources: list[SignalSource] = field(default_factory=list_signal_sources)

    async def collect(
        self,
        *,
        run: Any,
        step_index: int,
        context: SignalContext,
    ) -> list[SignalEvent]:
        all_events: list[SignalEvent] = []
        for source in self.sources:
            try:
                events = await source.collect(
                    run=run, step_index=step_index, context=context,
                )
                all_events.extend(events)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Signal source %r raised in collect", source.name,
                )
        return all_events
