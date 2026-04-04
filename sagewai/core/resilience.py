# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Resilience primitives — retry policies and circuit breakers."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit."""


@dataclass
class RetryPolicy:
    """Retry with exponential backoff for transient failures."""

    max_retries: int = 3
    backoff_base: float = 1.0

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        retryable_errors: tuple[type[Exception], ...] = (Exception,),
        **kwargs: Any,
    ) -> T:
        """Execute *fn* with retries on transient errors."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except retryable_errors as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = self.backoff_base * (2**attempt)
                    logger.warning(
                        "Retry %d/%d after %.1fs: %s",
                        attempt + 1,
                        self.max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            except Exception:
                raise
        raise last_error  # type: ignore[misc]


class CircuitBreaker:
    """Circuit breaker that opens after repeated failures.

    States:
      - CLOSED: Normal operation. Failures increment counter.
      - OPEN: Calls rejected with CircuitOpenError. Transitions to HALF_OPEN
        after *reset_timeout* seconds.
      - HALF_OPEN: One trial call allowed. Success → CLOSED, failure → OPEN.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    async def execute(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Execute *fn* through the circuit breaker."""
        current = self.state
        if current == CircuitState.OPEN:
            raise CircuitOpenError(
                f"Circuit is open. Retry after {self.reset_timeout}s."
            )

        try:
            result = await fn(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    def _record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit opened after %d failures", self._failure_count
            )
