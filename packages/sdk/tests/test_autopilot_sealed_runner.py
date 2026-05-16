# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sealed-aware runner and JIT-HITL stub — Plan K Tasks 5+6."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.autopilot.sealed_matcher import ProfileRecord
from sagewai.autopilot.controller.sealed_runner import (
    JitHitlPendingError,
    SealedToolRunner,
)


_NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)


async def _echo(args: dict) -> dict:
    return {"result": args.get("value", "ok"), "env": args.get("_env", {})}


def _profile(pid: str, scopes: set[str]) -> ProfileRecord:
    return ProfileRecord(id=pid, name=pid, granted_scopes=frozenset(scopes), last_used_at=_NOW)


# ── constructor ────────────────────────────────────────────────────────────


def test_runner_stores_profile():
    profile = _profile("p1", {"fs.read"})
    runner = SealedToolRunner(tools={"read_file": _echo}, profile=profile)
    assert runner.profile.id == "p1"


def test_runner_no_profile_is_none():
    runner = SealedToolRunner(tools={"read_file": _echo}, profile=None)
    assert runner.profile is None


# ── execute — profile provided ────────────────────────────────────────────


async def test_execute_calls_tool_when_profile_covers_scopes():
    profile = _profile("p1", {"fs.read"})
    runner = SealedToolRunner(tools={"read_file": _echo}, profile=profile)
    result = await runner.execute("read_file", {"value": "hello"})
    assert result["result"] == "hello"


async def test_execute_injects_profile_env_into_args():
    """Profile-injected env is passed as _env in tool args."""
    profile = _profile("p1", {"network.outbound.fetch"})
    # Patch the profile to have env — use a simple dict override
    import dataclasses
    profile_with_env = dataclasses.replace(profile)
    # We test that the runner passes _env when available
    runner = SealedToolRunner(
        tools={"web_search": _echo},
        profile=profile_with_env,
        profile_env={"API_KEY": "sk-test"},
    )
    result = await runner.execute("web_search", {})
    assert result["env"].get("API_KEY") == "sk-test"


# ── execute — JIT-HITL (no profile) ──────────────────────────────────────


async def test_execute_raises_jit_hitl_when_no_profile_and_scopes_required():
    """No profile + tool requires scopes → JitHitlPendingError."""
    runner = SealedToolRunner(tools={"web_search": _echo}, profile=None)
    with pytest.raises(JitHitlPendingError) as exc_info:
        await runner.execute("web_search", {})
    err = exc_info.value
    assert err.tool_name == "web_search"
    assert "network.outbound.fetch" in err.required_scopes


async def test_execute_no_scope_tool_runs_without_profile():
    """Tool with no scope requirements runs even without a profile."""
    runner = SealedToolRunner(tools={"math_eval": _echo}, profile=None)
    result = await runner.execute("math_eval", {"value": "42"})
    assert result["result"] == "42"


# ── JitHitlPendingError attributes ────────────────────────────────────────


async def test_jit_hitl_error_has_step_and_tool():
    runner = SealedToolRunner(
        tools={"shell_exec": _echo},
        profile=None,
        step_id="step-exec",
    )
    with pytest.raises(JitHitlPendingError) as exc_info:
        await runner.execute("shell_exec", {})
    err = exc_info.value
    assert err.tool_name == "shell_exec"
    assert err.step_id == "step-exec"
    assert "exec.shell" in err.required_scopes
