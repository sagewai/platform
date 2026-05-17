# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke test for example 51 — a long document on a small model (@transform)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sagewai"
    / "examples"
    / "51_big_input_small_model.py"
)


def _run() -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Force the offline (stub-LLM) path — no API key, no network.
    env.pop("SAGEWAI_TRANSFORM_LIVE", None)
    return subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_51_offline_path_completes() -> None:
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "offline (stub LLM)" in out
    # @transform(summarize, ...) compressed the document.
    assert "well inside the" in out
    # The custom registered operation ran over the raw document.
    assert "16 clauses" in out
    # The custom op honoured a directive param.
    assert "2 clauses — 5. DATA PROTECTION; 9. CUSTOMER DATA" in out
