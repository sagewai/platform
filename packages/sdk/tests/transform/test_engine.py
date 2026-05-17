# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the transform engine."""

import asyncio

import pytest

from sagewai.transform.engine import TransformEngine
from sagewai.transform.models import TransformRequest, TransformResult
from sagewai.transform.registry import TransformRegistry


async def _ok(content, *, project_id=None, **params):
    return "done"


async def _boom(content, *, project_id=None, **params):
    raise RuntimeError("kaboom")


async def _slow(content, *, project_id=None, **params):
    await asyncio.sleep(10)
    return "too late"


@pytest.mark.asyncio
async def test_engine_runs_op_and_wraps_str():
    reg = TransformRegistry()
    reg.register("ok", _ok)
    res = await TransformEngine(registry=reg).run(
        TransformRequest(operation="ok", content="x")
    )
    assert res.ok and res.output == "done"


@pytest.mark.asyncio
async def test_engine_unknown_op_returns_error_result():
    res = await TransformEngine(registry=TransformRegistry()).run(
        TransformRequest(operation="missing", content="x")
    )
    assert not res.ok and "unknown" in res.error.lower()


@pytest.mark.asyncio
async def test_engine_catches_op_exception():
    reg = TransformRegistry()
    reg.register("boom", _boom)
    res = await TransformEngine(registry=reg).run(
        TransformRequest(operation="boom", content="x")
    )
    assert not res.ok and "kaboom" in res.error


@pytest.mark.asyncio
async def test_engine_passes_through_transform_result():
    async def _result_op(content, *, project_id=None, **params):
        return TransformResult(operation="custom", output="rich", ok=True)

    reg = TransformRegistry()
    reg.register("custom", _result_op)
    res = await TransformEngine(registry=reg).run(
        TransformRequest(operation="custom", content="x")
    )
    assert res.ok and res.output == "rich" and res.operation == "custom"


@pytest.mark.asyncio
async def test_engine_timeout_returns_error_result():
    reg = TransformRegistry()
    reg.register("slow", _slow)
    res = await TransformEngine(registry=reg, timeout=0.05).run(
        TransformRequest(operation="slow", content="x")
    )
    assert not res.ok and res.error
