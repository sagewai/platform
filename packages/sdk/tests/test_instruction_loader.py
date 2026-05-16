# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for InstructionFileLoader — directory ancestor walk."""

from __future__ import annotations

from pathlib import Path

import pytest

from sagewai.directives.file_loader import (
    CHARS_PER_TOKEN,
    InstructionFileLoader,
)


def _write(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSingleFile:
    """Loading a single instruction file."""

    def test_loads_instructions_md(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "instructions.md", "Hello world")
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert result == "Hello world"

    def test_loads_agents_md(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "AGENTS.md", "Agent rules")
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert result == "Agent rules"

    def test_loads_both_files_same_dir(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "instructions.md", "Instructions")
        _write(tmp_path / ".sagewai" / "AGENTS.md", "Agents")
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert "Instructions" in result
        assert "Agents" in result
        # instructions.md comes first (order in INSTRUCTION_FILES)
        assert result.index("Instructions") < result.index("Agents")


class TestAncestorWalk:
    """Walking directory ancestors for instruction files."""

    def test_merges_root_first(self, tmp_path: Path) -> None:
        """Root-level instructions come first, then more specific."""
        root = tmp_path / "project"
        sub = root / "src" / "module"
        sub.mkdir(parents=True)

        _write(root / ".sagewai" / "instructions.md", "Root instructions")
        _write(sub / ".sagewai" / "instructions.md", "Module instructions")

        # Place a root marker at project level so walk stops there
        (root / ".git").mkdir()

        loader = InstructionFileLoader()
        result = loader.load(start_dir=sub)

        assert "Root instructions" in result
        assert "Module instructions" in result
        # Root comes first
        assert result.index("Root instructions") < result.index(
            "Module instructions"
        )

    def test_intermediate_dirs_included(self, tmp_path: Path) -> None:
        """Files at intermediate directories are included."""
        root = tmp_path / "project"
        mid = root / "src"
        leaf = mid / "deep"
        leaf.mkdir(parents=True)

        (root / ".git").mkdir()
        _write(root / ".sagewai" / "instructions.md", "Root")
        _write(mid / ".sagewai" / "instructions.md", "Mid")
        _write(leaf / ".sagewai" / "instructions.md", "Leaf")

        loader = InstructionFileLoader()
        result = loader.load(start_dir=leaf)

        parts = result.split("\n\n---\n\n")
        assert len(parts) == 3
        assert parts[0] == "Root"
        assert parts[1] == "Mid"
        assert parts[2] == "Leaf"


class TestDedup:
    """Content deduplication by hash."""

    def test_duplicate_content_included_once(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        sub = root / "sub"
        sub.mkdir(parents=True)

        (root / ".git").mkdir()
        _write(
            root / ".sagewai" / "instructions.md", "Same content"
        )
        _write(
            sub / ".sagewai" / "instructions.md", "Same content"
        )

        loader = InstructionFileLoader()
        result = loader.load(start_dir=sub)

        # Only one copy
        assert result == "Same content"

    def test_different_content_both_kept(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        sub = root / "sub"
        sub.mkdir(parents=True)

        (root / ".git").mkdir()
        _write(root / ".sagewai" / "instructions.md", "Content A")
        _write(sub / ".sagewai" / "instructions.md", "Content B")

        loader = InstructionFileLoader()
        result = loader.load(start_dir=sub)

        assert "Content A" in result
        assert "Content B" in result


class TestTokenBudget:
    """Token budget truncation."""

    def test_long_content_truncated(self, tmp_path: Path) -> None:
        budget = 10  # 10 tokens = 40 chars
        long_content = "x" * 200

        _write(tmp_path / ".sagewai" / "instructions.md", long_content)

        loader = InstructionFileLoader(token_budget=budget)
        result = loader.load(start_dir=tmp_path)

        max_chars = budget * CHARS_PER_TOKEN
        # Should be truncated (content + truncation notice)
        assert len(result) < len(long_content) + 100
        assert "[... instructions truncated" in result

    def test_short_content_not_truncated(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "instructions.md", "Short")

        loader = InstructionFileLoader(token_budget=1000)
        result = loader.load(start_dir=tmp_path)

        assert result == "Short"
        assert "truncated" not in result


class TestRootMarkerStop:
    """Root marker detection stops the walk."""

    def test_git_stops_walk(self, tmp_path: Path) -> None:
        """Walk stops at directory containing .git."""
        outer = tmp_path / "outer"
        inner = outer / "inner" / "project"
        inner.mkdir(parents=True)

        _write(
            outer / ".sagewai" / "instructions.md",
            "Should not appear",
        )
        (inner / ".git").mkdir()
        _write(
            inner / ".sagewai" / "instructions.md", "Project only"
        )

        loader = InstructionFileLoader()
        result = loader.load(start_dir=inner)

        assert result == "Project only"
        assert "Should not appear" not in result

    def test_pyproject_toml_stops_walk(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "project"
        inner.mkdir(parents=True)

        _write(
            outer / ".sagewai" / "instructions.md",
            "Outer",
        )
        (inner / "pyproject.toml").write_text("[project]\nname='x'")
        _write(inner / ".sagewai" / "instructions.md", "Inner")

        loader = InstructionFileLoader()
        result = loader.load(start_dir=inner)

        assert result == "Inner"
        assert "Outer" not in result

    def test_stop_at_root_false_walks_further(
        self, tmp_path: Path
    ) -> None:
        """When stop_at_root=False, walk past root markers."""
        outer = tmp_path / "outer"
        inner = outer / "project"
        inner.mkdir(parents=True)

        _write(outer / ".sagewai" / "instructions.md", "Outer")
        (inner / ".git").mkdir()
        _write(inner / ".sagewai" / "instructions.md", "Inner")

        loader = InstructionFileLoader(stop_at_root=False)
        result = loader.load(start_dir=inner)

        assert "Outer" in result
        assert "Inner" in result


class TestMissingFiles:
    """No instruction files found."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert result == ""

    def test_empty_file_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "instructions.md", "")
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert result == ""

    def test_whitespace_only_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / ".sagewai" / "instructions.md", "   \n\n  ")
        loader = InstructionFileLoader()
        result = loader.load(start_dir=tmp_path)
        assert result == ""


class TestCustomFiles:
    """Custom instruction_files parameter."""

    def test_custom_file_names(self, tmp_path: Path) -> None:
        _write(tmp_path / "RULES.md", "Custom rules")

        loader = InstructionFileLoader(
            instruction_files=["RULES.md"]
        )
        result = loader.load(start_dir=tmp_path)
        assert result == "Custom rules"

    def test_custom_overrides_default(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".sagewai" / "instructions.md",
            "Default file",
        )
        _write(tmp_path / "MY_RULES.md", "My rules")

        loader = InstructionFileLoader(
            instruction_files=["MY_RULES.md"]
        )
        result = loader.load(start_dir=tmp_path)
        assert result == "My rules"
        assert "Default file" not in result


class TestFilesystemRoot:
    """Reaching filesystem root doesn't crash."""

    def test_walk_to_fs_root(self, tmp_path: Path) -> None:
        """Walking with stop_at_root=False from tmp_path to /."""
        loader = InstructionFileLoader(stop_at_root=False)
        # Should not raise — walks to / and returns empty
        result = loader.load(start_dir=tmp_path)
        # Result depends on whether there happen to be instruction
        # files on the system; we just verify no crash.
        assert isinstance(result, str)


class TestIsRoot:
    """Root marker detection helper."""

    def test_detects_git(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        loader = InstructionFileLoader()
        assert loader._is_root(tmp_path) is True

    def test_detects_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        loader = InstructionFileLoader()
        assert loader._is_root(tmp_path) is True

    def test_no_marker(self, tmp_path: Path) -> None:
        loader = InstructionFileLoader()
        assert loader._is_root(tmp_path) is False

    def test_custom_root_markers(self, tmp_path: Path) -> None:
        (tmp_path / "MY_ROOT").touch()
        loader = InstructionFileLoader(root_markers=["MY_ROOT"])
        assert loader._is_root(tmp_path) is True
