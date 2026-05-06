"""Smoke test for example 35 — runs three real missions, no stub skip."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sagewai"
    / "examples"
    / "35_autopilot_hosted_service.py"
)


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _port_open("127.0.0.1", 8100),
    reason="local sagewai-llm not running on 127.0.0.1:8100",
)
def test_35_runs_three_missions_no_stub_skip() -> None:
    env = os.environ.copy()
    env["SAGEWAI_LLM_BASE_URL"] = "http://127.0.0.1:8100"
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # New requirement: no stub-skip branch
    assert "skipping mission run" not in out
    assert "stub-generated" not in out
    # Three mission runs printed
    assert out.count("Mission status:") >= 3
    # Performance table present
    assert "Performance summary" in out
