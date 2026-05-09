# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sandbox-aware ToolRunner — Plan J Task 4."""

from __future__ import annotations

import pytest

from sagewai.autopilot.controller.tool_runner import ToolRunner, SandboxViolationError
from sagewai.autopilot.tool_risk_profile import SandboxTier


# ── helpers ────────────────────────────────────────────────────────────────


async def _echo(args: dict) -> dict:
    return {"result": args.get("value", "ok")}


# ── constructor ────────────────────────────────────────────────────────────


def test_runner_default_tier_is_untrusted():
    """Default ToolRunner should require explicit tier opt-in (fail-secure)."""
    runner = ToolRunner(tools={"echo": _echo})
    assert runner.allowed_tier == SandboxTier.UNTRUSTED


def test_runner_accepts_explicit_tier():
    runner = ToolRunner(tools={"echo": _echo}, allowed_tier=SandboxTier.SANDBOXED)
    assert runner.allowed_tier == SandboxTier.SANDBOXED


# ── execute — allowed calls ────────────────────────────────────────────────


async def test_execute_calls_registered_tool():
    # "read_file" is TRUSTED in the registry, so allowed by a TRUSTED runner.
    runner = ToolRunner(tools={"read_file": _echo}, allowed_tier=SandboxTier.TRUSTED)
    result = await runner.execute("read_file", {"value": "hello"})
    assert result == {"result": "hello"}


async def test_trusted_tool_allowed_in_trusted_runner():
    runner = ToolRunner(tools={"read_file": _echo}, allowed_tier=SandboxTier.TRUSTED)
    result = await runner.execute("read_file", {})
    assert result is not None


async def test_sandboxed_tool_allowed_in_sandboxed_runner():
    runner = ToolRunner(tools={"web_search": _echo}, allowed_tier=SandboxTier.SANDBOXED)
    result = await runner.execute("web_search", {})
    assert result is not None


async def test_untrusted_tool_allowed_in_untrusted_runner():
    runner = ToolRunner(tools={"shell_exec": _echo}, allowed_tier=SandboxTier.UNTRUSTED)
    result = await runner.execute("shell_exec", {})
    assert result is not None


# ── execute — blocked calls ────────────────────────────────────────────────


async def test_untrusted_tool_blocked_in_trusted_runner():
    runner = ToolRunner(tools={"shell_exec": _echo}, allowed_tier=SandboxTier.TRUSTED)
    with pytest.raises(SandboxViolationError) as exc_info:
        await runner.execute("shell_exec", {})
    assert "shell_exec" in str(exc_info.value)
    assert "UNTRUSTED" in str(exc_info.value)


async def test_sandboxed_tool_blocked_in_trusted_runner():
    runner = ToolRunner(tools={"web_search": _echo}, allowed_tier=SandboxTier.TRUSTED)
    with pytest.raises(SandboxViolationError):
        await runner.execute("web_search", {})


async def test_untrusted_tool_blocked_in_sandboxed_runner():
    runner = ToolRunner(tools={"shell_exec": _echo}, allowed_tier=SandboxTier.SANDBOXED)
    with pytest.raises(SandboxViolationError):
        await runner.execute("shell_exec", {})


async def test_unknown_tool_raises_key_error():
    runner = ToolRunner(tools={}, allowed_tier=SandboxTier.UNTRUSTED)
    with pytest.raises(KeyError):
        await runner.execute("no_such_tool", {})


# ── SandboxViolationError attributes ──────────────────────────────────────


async def test_sandbox_violation_error_has_tool_and_tier():
    runner = ToolRunner(tools={"shell_exec": _echo}, allowed_tier=SandboxTier.TRUSTED)
    with pytest.raises(SandboxViolationError) as exc_info:
        await runner.execute("shell_exec", {})
    err = exc_info.value
    assert err.tool_name == "shell_exec"
    assert err.required_tier == SandboxTier.UNTRUSTED
    assert err.allowed_tier == SandboxTier.TRUSTED
