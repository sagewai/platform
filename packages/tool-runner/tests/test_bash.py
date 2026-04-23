"""Tests for the in-sandbox bash tool."""
import pytest

from sagewai_tool_runner.tools.bash import run_bash


@pytest.mark.asyncio
async def test_bash_echo_success():
    res = await run_bash({"command": "echo hi"}, timeout_s=5)
    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "hi"


@pytest.mark.asyncio
async def test_bash_nonzero_exit():
    res = await run_bash({"command": "exit 7"}, timeout_s=5)
    assert res["ok"] is False
    assert res["exit_code"] == 7


@pytest.mark.asyncio
async def test_bash_timeout():
    res = await run_bash({"command": "sleep 10"}, timeout_s=0.2)
    assert res["ok"] is False
    assert "timeout" in res["error"].lower()


@pytest.mark.asyncio
async def test_bash_stderr_captured():
    res = await run_bash({"command": "echo warn >&2; echo ok"}, timeout_s=5)
    assert res["ok"] is True
    assert res["stdout"].strip() == "ok"
    assert res["stderr"].strip() == "warn"


@pytest.mark.asyncio
async def test_bash_rejects_missing_command():
    res = await run_bash({}, timeout_s=5)
    assert res["ok"] is False
    assert "command" in res["error"]
