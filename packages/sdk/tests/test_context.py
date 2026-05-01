# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for project-scoped isolation module."""

from __future__ import annotations

import asyncio
import time

import pytest

from sagewai.core.context import (
    ProjectContext,
    ProjectQuota,
    ProjectQuotaExceededError,
    ProjectRateLimitError,
    ProjectRequiredError,
    get_current_project,
    require_project,
)

# ------------------------------------------------------------------
# ProjectContext basics
# ------------------------------------------------------------------


def test_default_namespace_equals_project_id():
    ctx = ProjectContext(project_id="acme")
    assert ctx.namespace == "acme"


def test_custom_namespace():
    ctx = ProjectContext(project_id="acme", namespace="acme-prod")
    assert ctx.namespace == "acme-prod"


def test_scoped_collection():
    ctx = ProjectContext(project_id="acme")
    assert ctx.scoped_collection("documents") == "acme__documents"
    assert ctx.scoped_collection("vectors") == "acme__vectors"


def test_scoped_key():
    ctx = ProjectContext(project_id="acme")
    assert ctx.scoped_key("session:123") == "acme:session:123"


def test_metadata():
    ctx = ProjectContext(project_id="acme", metadata={"plan": "enterprise", "region": "us-east"})
    assert ctx.metadata["plan"] == "enterprise"


# ------------------------------------------------------------------
# Context propagation — sync
# ------------------------------------------------------------------


def test_sync_context_manager():
    assert get_current_project() is None
    ctx = ProjectContext(project_id="acme")
    with ctx:
        assert get_current_project() is ctx
        assert require_project() is ctx
    assert get_current_project() is None


def test_nested_sync_contexts():
    outer = ProjectContext(project_id="outer")
    inner = ProjectContext(project_id="inner")
    with outer:
        assert get_current_project() is outer
        with inner:
            assert get_current_project() is inner
        assert get_current_project() is outer


# ------------------------------------------------------------------
# Context propagation — async
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_context_manager():
    assert get_current_project() is None
    ctx = ProjectContext(project_id="acme")
    async with ctx:
        assert get_current_project() is ctx
    assert get_current_project() is None


@pytest.mark.asyncio
async def test_context_isolated_across_tasks():
    """Each asyncio task should have its own project context."""
    results: dict[str, str | None] = {}

    async def task_a():
        ctx = ProjectContext(project_id="task-a")
        async with ctx:
            await asyncio.sleep(0.01)
            t = get_current_project()
            results["a"] = t.project_id if t else None

    async def task_b():
        ctx = ProjectContext(project_id="task-b")
        async with ctx:
            await asyncio.sleep(0.01)
            t = get_current_project()
            results["b"] = t.project_id if t else None

    await asyncio.gather(task_a(), task_b())
    assert results["a"] == "task-a"
    assert results["b"] == "task-b"


# ------------------------------------------------------------------
# Require project
# ------------------------------------------------------------------


def test_require_project_raises_when_no_context():
    with pytest.raises(ProjectRequiredError, match="No project context"):
        require_project()


# ------------------------------------------------------------------
# Rate limiting
# ------------------------------------------------------------------


def test_rate_limit_passes_under_quota():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_requests_per_minute=5))
    for _ in range(5):
        ctx.check_rate_limit()
        ctx.record_usage()


def test_rate_limit_raises_when_exceeded():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_requests_per_minute=2))
    ctx.record_usage()
    ctx.record_usage()
    with pytest.raises(ProjectRateLimitError, match="exceeded 2 requests/min"):
        ctx.check_rate_limit()


def test_rate_limit_resets_after_window():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_requests_per_minute=1))
    ctx.record_usage()
    # Simulate time passing
    ctx._usage.window_start = time.time() - 61
    ctx.check_rate_limit()  # should not raise


# ------------------------------------------------------------------
# Token quota
# ------------------------------------------------------------------


def test_token_quota_passes_under_limit():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_tokens_per_minute=1000))
    ctx.check_token_quota(500)
    ctx.record_usage(tokens=500)
    ctx.check_token_quota(499)


def test_token_quota_raises_when_exceeded():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_tokens_per_minute=1000))
    ctx.record_usage(tokens=800)
    with pytest.raises(ProjectQuotaExceededError, match="1000 tokens/min"):
        ctx.check_token_quota(201)


# ------------------------------------------------------------------
# Cost quota
# ------------------------------------------------------------------


def test_cost_quota_passes_under_limit():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_cost_per_day_usd=10.0))
    ctx.check_cost_quota(5.0)
    ctx.record_usage(cost=5.0)
    ctx.check_cost_quota(4.99)


def test_cost_quota_raises_when_exceeded():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_cost_per_day_usd=10.0))
    ctx.record_usage(cost=8.0)
    with pytest.raises(ProjectQuotaExceededError, match="\\$10.0/day"):
        ctx.check_cost_quota(3.0)


def test_cost_quota_resets_after_day():
    ctx = ProjectContext(project_id="acme", quota=ProjectQuota(max_cost_per_day_usd=1.0))
    ctx.record_usage(cost=1.0)
    # Simulate day passing
    ctx._usage.day_start = time.time() - 86401
    ctx.check_cost_quota(0.5)  # should not raise


# ------------------------------------------------------------------
# Usage tracking
# ------------------------------------------------------------------


def test_usage_tracking():
    ctx = ProjectContext(project_id="acme")
    ctx.record_usage(tokens=100, cost=0.01)
    ctx.record_usage(tokens=200, cost=0.02)
    assert ctx.usage.tokens_used == 300
    assert ctx.usage.requests_made == 2
    assert abs(ctx.usage.cost_usd - 0.03) < 1e-9
