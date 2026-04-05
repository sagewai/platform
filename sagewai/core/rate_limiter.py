# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Rate limiting for agent LLM calls and tool executions.

Provides token bucket and sliding window rate limiters to prevent
resource exhaustion and enforce fair usage across agents and projects.

Usage::

    from sagewai.core.rate_limiter import RateLimiter, TokenBucketLimiter

    # 10 LLM calls per minute
    limiter = TokenBucketLimiter(rate=10, period=60.0)

    # Before each LLM call:
    await limiter.acquire()  # blocks until a token is available

    # Or check without blocking:
    if limiter.try_acquire():
        # proceed
    else:
        # rate limited

    # Composite limiter for multiple limits
    limiter = RateLimiter(
        llm_limiter=TokenBucketLimiter(rate=20, period=60.0),
        tool_limiter=SlidingWindowLimiter(max_calls=100, window_seconds=60.0),
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded and blocking is not desired."""

    def __init__(self, limiter_name: str, retry_after: float = 0.0) -> None:
        super().__init__(
            f"Rate limit exceeded for '{limiter_name}'. "
            f"Retry after {retry_after:.1f}s"
        )
        self.limiter_name = limiter_name
        self.retry_after = retry_after


class TokenBucketLimiter:
    """Token bucket rate limiter.

    Allows ``rate`` operations per ``period`` seconds. Tokens refill
    continuously. Supports both blocking (acquire) and non-blocking
    (try_acquire) modes.

    Args:
        rate: Maximum number of operations per period.
        period: Time window in seconds (default: 60.0 = per minute).
        burst: Maximum burst size. Defaults to ``rate`` (no extra burst).
        name: Limiter name for error messages.
    """

    def __init__(
        self,
        rate: int,
        period: float = 60.0,
        burst: int | None = None,
        name: str = "token_bucket",
    ) -> None:
        self.rate = rate
        self.period = period
        self.burst = burst or rate
        self.name = name
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.burst,
            self._tokens + elapsed * (self.rate / self.period),
        )
        self._last_refill = now

    def try_acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens without blocking.

        Returns ``False`` if insufficient tokens are available.
        """
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, blocking until available."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Calculate wait time for next token
                deficit = tokens - self._tokens
                wait_time = deficit * (self.period / self.rate)
                await asyncio.sleep(min(wait_time, 1.0))

    @property
    def available_tokens(self) -> float:
        """Current available tokens (approximate)."""
        self._refill()
        return self._tokens

    def time_until_available(self, tokens: int = 1) -> float:
        """Seconds until ``tokens`` will be available."""
        self._refill()
        if self._tokens >= tokens:
            return 0.0
        deficit = tokens - self._tokens
        return deficit * (self.period / self.rate)


class SlidingWindowLimiter:
    """Sliding window rate limiter.

    Tracks exact timestamps of recent calls. More accurate than
    token bucket for bursty workloads but uses more memory.

    Args:
        max_calls: Maximum calls allowed in the window.
        window_seconds: Window size in seconds.
        name: Limiter name for error messages.
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: float = 60.0,
        name: str = "sliding_window",
    ) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.name = name
        self._timestamps: deque[float] = deque()

    def _cleanup(self) -> None:
        """Remove timestamps outside the window."""
        cutoff = time.monotonic() - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def try_acquire(self) -> bool:
        """Try to record a call. Returns ``False`` if limit exceeded."""
        self._cleanup()
        if len(self._timestamps) >= self.max_calls:
            return False
        self._timestamps.append(time.monotonic())
        return True

    async def acquire(self) -> None:
        """Record a call, blocking until allowed."""
        while True:
            self._cleanup()
            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(time.monotonic())
                return
            # Wait until oldest entry expires
            oldest = self._timestamps[0]
            wait = (oldest + self.window_seconds) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(min(wait, 1.0))

    @property
    def current_count(self) -> int:
        """Number of calls in the current window."""
        self._cleanup()
        return len(self._timestamps)

    @property
    def remaining(self) -> int:
        """Remaining calls allowed in current window."""
        self._cleanup()
        return max(0, self.max_calls - len(self._timestamps))


class RateLimiter:
    """Composite rate limiter for agent operations.

    Combines separate limiters for LLM calls and tool executions.
    Can be attached to a :class:`~sagewai.core.base.BaseAgent` to enforce
    limits automatically.

    Args:
        llm_limiter: Rate limiter for LLM calls. Applied before each
            ``_call_llm``.
        tool_limiter: Rate limiter for tool executions. Applied before
            each ``_execute_tool``.
        name: Identifier for this limiter (e.g. agent name or project ID).
    """

    def __init__(
        self,
        *,
        llm_limiter: TokenBucketLimiter | SlidingWindowLimiter | None = None,
        tool_limiter: TokenBucketLimiter | SlidingWindowLimiter | None = None,
        name: str = "default",
    ) -> None:
        self.llm_limiter = llm_limiter
        self.tool_limiter = tool_limiter
        self.name = name

    async def check_llm(self) -> None:
        """Check LLM rate limit. Blocks until allowed."""
        if self.llm_limiter:
            await self.llm_limiter.acquire()

    async def check_tool(self) -> None:
        """Check tool rate limit. Blocks until allowed."""
        if self.tool_limiter:
            await self.tool_limiter.acquire()

    def try_llm(self) -> bool:
        """Non-blocking LLM rate check."""
        if self.llm_limiter:
            return self.llm_limiter.try_acquire()
        return True

    def try_tool(self) -> bool:
        """Non-blocking tool rate check."""
        if self.tool_limiter:
            return self.tool_limiter.try_acquire()
        return True
