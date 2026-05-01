# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Smoke test — all examples parse without syntax errors."""

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _example_files():
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(EXAMPLES_DIR.glob("*.py"))


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_parses(path):
    source = path.read_text()
    ast.parse(source, filename=str(path))
