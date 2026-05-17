# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end chain: example 30 writes JSONL → example 36 cycle-2 ingests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


SDK = Path(__file__).resolve().parents[2] / "sagewai"


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
def test_30_then_36_picks_up_captured_runs(tmp_path: Path) -> None:
    home = tmp_path / "fakehome"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["SAGEWAI_LLM_BASE_URL"] = "http://127.0.0.1:8100"
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)

    proc_30 = subprocess.run(
        [sys.executable, str(SDK / "examples" / "30_oncall_agent.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc_30.returncode == 0, proc_30.stderr

    runs = list(
        (home / ".sagewai" / "training_runs").rglob("*.jsonl")
    )
    assert runs, "example 30 must write at least one JSONL"

    proc_36 = subprocess.run(
        [sys.executable, str(SDK / "examples" / "36_autopilot_training_loop.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc_36.returncode == 0, proc_36.stderr
    out_36 = proc_36.stdout
    assert "captured runs loaded from" in out_36
    # cycle-2 ingested at least 1 run from disk
    assert "(0 runs)" not in out_36
