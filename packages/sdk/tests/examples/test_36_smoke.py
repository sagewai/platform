# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke test for example 36 — cycle-1 + cycle-2 dry path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sagewai"
    / "examples"
    / "36_autopilot_training_loop.py"
)


def test_36_dry_path_runs_two_cycles_no_live_fine_tune(tmp_path: Path) -> None:
    """Without SAGEWAI_FT_LIVE, both cycles run as dry passes."""
    home = tmp_path / "fakehome"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("SAGEWAI_FT_LIVE", None)
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Cycle 1" in out
    assert "Cycle 2" in out
    # Both cycles produce a fine-tune job payload (printed)
    assert "job_id" in out
    assert "(SAGEWAI_FT_LIVE not set" in out or "skipping live fine-tune" in out


def test_36_cycle_2_loads_captured_runs(tmp_path: Path) -> None:
    """When ~/.sagewai/training_runs has captures, cycle-2 ingests them."""
    home = tmp_path / "fakehome"
    runs_dir = home / ".sagewai" / "training_runs" / "test-instance"
    runs_dir.mkdir(parents=True)
    sample_path = runs_dir / "captured-001.jsonl"
    sample = {
        "mission_id": "captured-001",
        "project_id": "acme-prod",
        "blueprint_id": "oncall-triage",
        "status": "completed",
        "duration_seconds": 1.4,
        "prompt": "triage the incident: high CPU on prod-web-01",
        "completion": '{"urgency": "high", "action": "scale up"}',
        "model_used": "claude-haiku-4-5-20251001",
        "user_rating": 5,
        "human_override": False,
    }
    sample_path.write_text(json.dumps(sample) + "\n")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("SAGEWAI_FT_LIVE", None)
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Cycle-2 picked up the real captured run
    assert "Cycle 2" in out
    assert (
        "captured runs loaded" in out
        or "captured-001" in out
        or "from ~/.sagewai/training_runs" in out
    )


def test_36_live_path_skips_cleanly_when_mlx_unavailable(
    tmp_path: Path,
) -> None:
    """SAGEWAI_FT_LIVE=1 without mlx-tune installed prints skip message."""
    home = tmp_path / "fakehome"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["SAGEWAI_FT_LIVE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Either we ran live (Apple Silicon + mlx-tune present) or we
    # printed the skip message. Both are acceptable.
    assert (
        "skipping live fine-tune" in out
        or "not Apple Silicon" in out
        or "adapter saved to" in out
    )
