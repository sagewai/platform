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
