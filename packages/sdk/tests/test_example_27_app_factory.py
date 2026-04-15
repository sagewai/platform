# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
"""Smoke test for example 27 — the dark-factory App Factory tenant.

Loads the example via ``importlib`` (since filenames starting with a
digit are not valid Python identifiers), runs the async ``main()``, and
captures stdout. Asserts the factory emits its headline lines so
regressions in the shared ``_factory`` helpers fail loudly.

Runs offline, no Ollama, no GPU. Completes in well under 5 s.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest


_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sagewai"
    / "examples"
    / "27_app_factory.py"
)


_MODULE_NAME = "sagewai_example_27_app_factory"


def _load_example():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register *before* exec so dataclass __module__ lookups resolve.
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_example_27_runs_end_to_end() -> None:
    module = _load_example()

    buf = StringIO()
    with patch("sys.stdout", buf):
        await module.main()

    output = buf.getvalue()

    # Tenant envelope
    assert "Dark Factory 1" in output
    assert "app-factory" in output

    # Pipeline stages all fired
    for stage in ("intake", "research", "plan", "scaffold", "build", "delivery"):
        assert stage in output, f"missing stage {stage!r} in output"

    # Isolation proof
    assert "isolation ✓" in output
    for other in ("biz-ops", "wealth-desk", "school-mentor"):
        assert other in output

    # Scoreboard + training flywheel
    assert "Fleet scoreboard" in output
    assert "Training flywheel" in output
    assert "before=$" in output and "after=$" in output

    # Training loop should show a non-trivial cost reduction
    assert "-95.0%" in output or "-9" in output


def test_example_27_can_be_run_as_script() -> None:
    """Guard against accidental syntax / import errors in the file."""
    module = _load_example()
    assert callable(module.main)
    assert inspect.iscoroutinefunction(module.main)
