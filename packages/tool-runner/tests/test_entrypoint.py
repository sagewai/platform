"""Integration test: drive the runner over stdin/stdout."""
import asyncio
import json
import sys

import pytest


@pytest.mark.asyncio
async def test_runner_round_trip_bash():
    """Spawn the runner as a subprocess and send one JSON-RPC exec request."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sagewai_tool_runner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    req = {
        "jsonrpc": "2.0",
        "method": "exec",
        "params": {
            "tool": "bash",
            "args": {"command": "echo hello"},
            "call_id": "c1",
            "timeout_s": 5,
        },
        "id": 1,
    }
    line = (json.dumps(req) + "\n").encode()
    proc.stdin.write(line)
    await proc.stdin.drain()
    proc.stdin.close()

    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    first_line = stdout.decode("utf-8").splitlines()[0]
    resp = json.loads(first_line)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["ok"] is True
    assert resp["result"]["stdout"].strip() == "hello"


@pytest.mark.asyncio
async def test_runner_unknown_method():
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sagewai_tool_runner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    req = {"jsonrpc": "2.0", "method": "nope", "params": {}, "id": 2}
    proc.stdin.write((json.dumps(req) + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()

    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    first_line = stdout.decode("utf-8").splitlines()[0]
    resp = json.loads(first_line)
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_runner_version_flag():
    """--version prints the package version and exits 0."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sagewai_tool_runner",
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    assert proc.returncode == 0
    from sagewai_tool_runner import __version__
    assert stdout.decode("utf-8").strip() == __version__
