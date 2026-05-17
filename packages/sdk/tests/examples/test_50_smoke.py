# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke test for example 50 — incident knowledge graph (@transform directive)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sagewai"
    / "examples"
    / "50_incident_knowledge_graph.py"
)


def _run() -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Force the offline (stub-extractor) path — no API key, no network.
    env.pop("SAGEWAI_TRANSFORM_LIVE", None)
    return subprocess.run(
        [sys.executable, str(EXAMPLE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_50_offline_path_completes() -> None:
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "offline (stub extractor)" in out
    # The @transform(graphify, ...) directive ran and wrote relations.
    assert "relations into graph memory" in out
    # The second incident retrieves the connected sub-graph from memory.
    assert "billing --[depends-on]--> payments" in out
    assert "payments --[depends-on]--> auth" in out
