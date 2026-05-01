# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for resilience module — retry policy and circuit breaker."""

from __future__ import annotations

import asyncio

import pytest

from sagewai.core.resilience import CircuitBreaker, CircuitState, RetryPolicy

# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_default_config(self):
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.backoff_base == 1.0

    def test_custom_config(self):
        policy = RetryPolicy(max_retries=5, backoff_base=0.5)
        assert policy.max_retries == 5

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        policy = RetryPolicy(max_retries=3)
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await policy.execute(fn)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        policy = RetryPolicy(max_retries=3, backoff_base=0.01)
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "recovered"

        result = await policy.execute(fn, retryable_errors=(ConnectionError,))
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        policy = RetryPolicy(max_retries=2, backoff_base=0.01)

        async def fn():
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError, match="always fails"):
            await policy.execute(fn, retryable_errors=(ConnectionError,))

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self):
        policy = RetryPolicy(max_retries=3, backoff_base=0.01)
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await policy.execute(fn, retryable_errors=(ConnectionError,))
        assert call_count == 1


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_default_config(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.reset_timeout == 60.0
        assert cb.state == CircuitState.CLOSED

    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_stays_closed_on_success(self):
        cb = CircuitBreaker(failure_threshold=3)

        async def fn():
            return "ok"

        result = await cb.execute(fn)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=0.01)

        async def fn():
            raise RuntimeError("fail")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.execute(fn)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_raises_circuit_open_error(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=100.0)

        async def fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await cb.execute(fn)

        from sagewai.core.resilience import CircuitOpenError

        with pytest.raises(CircuitOpenError):
            await cb.execute(fn)

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)

        async def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await cb.execute(fail)

        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_closes_on_success_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)

        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fail")
            return "recovered"

        with pytest.raises(RuntimeError):
            await cb.execute(fn)

        await asyncio.sleep(0.02)
        result = await cb.execute(fn)
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    def test_record_success_resets_failures(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb._failure_count = 3
        cb._record_success()
        assert cb._failure_count == 0
