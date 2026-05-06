"""Smoke test for example 28 — both offline and online code paths.

The test runs the example as a subprocess so we exercise the actual
script entrypoint (not just imported helpers). We verify:

- Offline path: SAGEWAI_LLM_BASE_URL unset → existing dead-transport
  output present; no stub markers.
- Online path: SAGEWAI_LLM_BASE_URL=http://127.0.0.1:8100 *and* the
  port is open → three routing-decision lines printed (one per demo
  goal). When the port is closed (CI without docker compose), this
  assertion is skipped, not failed.
"""

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
    / "28_autopilot_quickstart.py"
)


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    # Drop any inherited SAGEWAI_LLM_BASE_URL unless explicitly set.
    if env_overrides is None or "SAGEWAI_LLM_BASE_URL" not in env_overrides:
        env.pop("SAGEWAI_LLM_BASE_URL", None)
    return subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_28_offline_path_runs_and_has_no_stub_markers() -> None:
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Done." in out
    assert "stub-generated" not in out
    assert "skipped because stub" not in out
    # Offline path advertises itself
    assert "offline" in out.lower() or "synthesis" in out.lower()


@pytest.mark.skipif(
    not _port_open("127.0.0.1", 8100),
    reason="local sagewai-llm not running on 127.0.0.1:8100",
)
def test_28_online_path_prints_three_routing_decisions() -> None:
    proc = _run({"SAGEWAI_LLM_BASE_URL": "http://127.0.0.1:8100"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Three demo goals → three routing lines
    assert out.count("routing result:") >= 3
    # We expect at least one auto-routed match against the seed corpus
    # and at least one synthesis-needed (off-topic) decision.
    assert "auto_routed" in out
    assert "synthesis_needed" in out
    assert "stub-generated" not in out
