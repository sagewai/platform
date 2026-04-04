"""Tests for environment modes module."""

from __future__ import annotations

import json

import pytest

from sagewai.core.environment import (
    EnvironmentConfig,
    EnvironmentMode,
    get_current_mode,
    set_global_mode,
)
from sagewai.models.tool import ToolResult

# ------------------------------------------------------------------
# EnvironmentMode enum
# ------------------------------------------------------------------


def test_mode_values():
    assert EnvironmentMode.SIMULATION == "simulation"
    assert EnvironmentMode.STAGING == "staging"
    assert EnvironmentMode.PRODUCTION == "production"


def test_mode_from_string():
    assert EnvironmentMode("simulation") is EnvironmentMode.SIMULATION


# ------------------------------------------------------------------
# Context manager
# ------------------------------------------------------------------


def test_sync_context_sets_mode():
    env = EnvironmentConfig(mode=EnvironmentMode.STAGING)
    assert get_current_mode() is None
    with env:
        assert get_current_mode() is EnvironmentMode.STAGING
    assert get_current_mode() is None


@pytest.mark.asyncio
async def test_async_context_sets_mode():
    env = EnvironmentConfig(mode=EnvironmentMode.SIMULATION)
    assert get_current_mode() is None
    async with env:
        assert get_current_mode() is EnvironmentMode.SIMULATION
    assert get_current_mode() is None


def test_nested_contexts():
    outer = EnvironmentConfig(mode=EnvironmentMode.PRODUCTION)
    inner = EnvironmentConfig(mode=EnvironmentMode.SIMULATION)
    with outer:
        assert get_current_mode() is EnvironmentMode.PRODUCTION
        with inner:
            assert get_current_mode() is EnvironmentMode.SIMULATION
        assert get_current_mode() is EnvironmentMode.PRODUCTION


# ------------------------------------------------------------------
# SIMULATION mode
# ------------------------------------------------------------------


def test_simulation_returns_synthetic_result():
    env = EnvironmentConfig(mode=EnvironmentMode.SIMULATION)
    result = env.wrap_tool_result("search", "tc_1", {"query": "hello"})
    assert result is not None
    assert result.name == "search"
    assert result.tool_call_id == "tc_1"
    parsed = json.loads(result.content)
    assert parsed["status"] == "simulated"
    assert parsed["tool"] == "search"


def test_simulation_custom_response():
    env = EnvironmentConfig(
        mode=EnvironmentMode.SIMULATION,
        simulation_responses={"search": '{"results": ["mock1", "mock2"]}'},
    )
    result = env.wrap_tool_result("search", "tc_1", {"query": "test"})
    assert result is not None
    parsed = json.loads(result.content)
    assert parsed["results"] == ["mock1", "mock2"]


def test_simulation_should_mock():
    env = EnvironmentConfig(mode=EnvironmentMode.SIMULATION)
    assert env.should_mock() is True


# ------------------------------------------------------------------
# STAGING mode
# ------------------------------------------------------------------


def test_staging_logs_audit_entry():
    env = EnvironmentConfig(mode=EnvironmentMode.STAGING)
    real_result = ToolResult(tool_call_id="tc_1", name="search", content="real data")
    result = env.wrap_tool_result("search", "tc_1", {"query": "hello"}, real_result=real_result)
    # STAGING does not intercept — returns None
    assert result is None
    assert len(env.audit_log) == 1
    entry = env.audit_log[0]
    assert entry["tool"] == "search"
    assert entry["arguments"] == {"query": "hello"}
    assert entry["result"] == "real data"
    assert entry["error"] is None


def test_staging_should_not_mock():
    env = EnvironmentConfig(mode=EnvironmentMode.STAGING)
    assert env.should_mock() is False


def test_staging_audit_log_accumulates():
    env = EnvironmentConfig(mode=EnvironmentMode.STAGING)
    for i in range(3):
        env.wrap_tool_result(f"tool_{i}", f"tc_{i}", {})
    assert len(env.audit_log) == 3


def test_clear_audit_log():
    env = EnvironmentConfig(mode=EnvironmentMode.STAGING)
    env.wrap_tool_result("tool", "tc_1", {})
    assert len(env.audit_log) == 1
    env.clear_audit_log()
    assert len(env.audit_log) == 0


# ------------------------------------------------------------------
# PRODUCTION mode
# ------------------------------------------------------------------


def test_production_does_not_intercept():
    env = EnvironmentConfig(mode=EnvironmentMode.PRODUCTION)
    result = env.wrap_tool_result("search", "tc_1", {"query": "hello"})
    assert result is None
    assert len(env.audit_log) == 0


def test_production_should_not_mock():
    env = EnvironmentConfig(mode=EnvironmentMode.PRODUCTION)
    assert env.should_mock() is False


# ------------------------------------------------------------------
# Global mode
# ------------------------------------------------------------------


def test_set_global_mode():
    set_global_mode(EnvironmentMode.STAGING)
    assert get_current_mode() is EnvironmentMode.STAGING
    # Clean up
    set_global_mode(EnvironmentMode.PRODUCTION)


# ------------------------------------------------------------------
# Default config
# ------------------------------------------------------------------


def test_default_mode_is_production():
    env = EnvironmentConfig()
    assert env.mode is EnvironmentMode.PRODUCTION
