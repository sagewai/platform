# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Health monitor with circuit breaker for connectors."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sagewai.connectors.base import HealthStatus

logger = logging.getLogger(__name__)

DEGRADE_THRESHOLD = 3
DISCONNECT_THRESHOLD = 10


class HealthMonitor:
    """Tracks connector health with circuit breaker pattern."""

    def __init__(self, check_interval: timedelta = timedelta(minutes=5)) -> None:
        self._check_interval = check_interval
        self._failure_counts: dict[str, int] = {}
        self._statuses: dict[str, HealthStatus] = {}
        self._task: asyncio.Task | None = None

    def status(self, connector_name: str) -> HealthStatus:
        """Get last known health status."""
        return self._statuses.get(
            connector_name,
            HealthStatus(status="disconnected"),
        )

    def _record_failure(self, connector_name: str, error: str = "") -> None:
        count = self._failure_counts.get(connector_name, 0) + 1
        self._failure_counts[connector_name] = count
        if count >= DISCONNECT_THRESHOLD:
            status = "disconnected"
        elif count >= DEGRADE_THRESHOLD:
            status = "degraded"
        else:
            status = "healthy"
        self._statuses[connector_name] = HealthStatus(
            status=status,
            error=error or None,
            last_check=datetime.now(timezone.utc).isoformat(),
        )

    def _record_success(
        self,
        connector_name: str,
        latency_ms: int = 0,
        tool_count: int = 0,
    ) -> None:
        self._failure_counts[connector_name] = 0
        self._statuses[connector_name] = HealthStatus(
            status="healthy",
            latency_ms=latency_ms,
            tool_count=tool_count,
            last_check=datetime.now(timezone.utc).isoformat(),
        )

    async def start(self, registry: object) -> None:
        """Begin periodic health checks. Pass a ConnectorRegistry."""
        self._registry = registry
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop health monitoring."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval.total_seconds())
            if hasattr(self, '_registry'):
                await self._check_all_with_reconnect(self._registry)
            else:
                await self._check_all()

    async def _check_all_with_reconnect(self, registry) -> None:
        """Check health and attempt reconnection for failed connectors."""
        for connector in registry.list():
            try:
                creds = await registry._resolver.resolve(connector)
                hs = await connector.health_check(creds)
                if hs.status == "healthy":
                    self._record_success(
                        connector.name,
                        latency_ms=hs.latency_ms or 0,
                        tool_count=hs.tool_count or 0,
                    )
                else:
                    self._record_failure(connector.name, hs.error or "")
                    # Attempt reconnect if previously connected
                    if connector.name in registry._connections:
                        try:
                            await registry.disconnect(connector.name)
                            await registry.connect(connector.name, creds)
                            self._record_success(connector.name)
                        except Exception as e:
                            self._record_failure(
                                connector.name, f"Reconnect failed: {e}"
                            )
            except Exception as e:
                self._record_failure(connector.name, str(e))

    async def _check_all(self) -> None:
        registry = self._registry
        for connector in registry.list():
            try:
                creds = await registry._resolver.resolve(connector)
                hs = await connector.health_check(creds)
                if hs.status == "healthy":
                    self._record_success(
                        connector.name,
                        latency_ms=hs.latency_ms or 0,
                        tool_count=hs.tool_count or 0,
                    )
                else:
                    self._record_failure(connector.name, hs.error or "")
            except Exception as e:
                self._record_failure(connector.name, str(e))
