# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Smoke test for example 30 — retrieves on-call blueprint, runs mission."""

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
    / "30_oncall_agent.py"
)


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_30_offline_path_completes_without_api_key() -> None:
    proc = _run({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "SAGEWAI_LLM_BASE_URL": ""})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Mission status:" in out
    assert "stub-generated" not in out


@pytest.mark.skipif(
    not _port_open("127.0.0.1", 8100),
    reason="local sagewai-llm not running on 127.0.0.1:8100",
)
def test_30_retrieves_oncall_blueprint_from_seed_corpus() -> None:
    proc = _run(
        {
            "SAGEWAI_LLM_BASE_URL": "http://127.0.0.1:8100",
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Blueprint id from Plan C's seed corpus
    assert "oncall-triage" in out
    # Routing decision printed
    assert "routing result:" in out
    # Mission ran end-to-end
    assert "Mission status:" in out
    assert "stub-generated" not in out


@pytest.mark.skipif(
    not _port_open("127.0.0.1", 8100),
    reason="local sagewai-llm not running on 127.0.0.1:8100",
)
def test_30_writes_training_run_jsonl(tmp_path: Path) -> None:
    home = tmp_path / "fakehome"
    home.mkdir()
    proc = _run(
        {
            "SAGEWAI_LLM_BASE_URL": "http://127.0.0.1:8100",
            "HOME": str(home),
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }
    )
    assert proc.returncode == 0, proc.stderr
    runs_dir = home / ".sagewai" / "training_runs"
    assert runs_dir.exists(), "training_runs dir should be created"
    # At least one JSONL file written
    jsonl_files = list(runs_dir.rglob("*.jsonl"))
    assert jsonl_files, f"expected JSONL run capture under {runs_dir}"
