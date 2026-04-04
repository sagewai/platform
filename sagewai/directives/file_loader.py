# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""InstructionFileLoader — load instruction files by walking directory ancestors.

Walks from a starting directory up to the filesystem root (or a project root
marker), loading ``.sagewai/instructions.md`` files at each level. Instructions
are merged root-first (root-level instructions form the base, more specific
directories override).

Inspired by Claude Code's CLAUDE.md ancestor walk pattern.

Usage::

    from sagewai.directives.file_loader import InstructionFileLoader

    loader = InstructionFileLoader()
    instructions = loader.load()  # walks from cwd upward

    # Or specify a starting directory
    instructions = loader.load(start_dir="/path/to/project/src/module")
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Files to look for at each directory level
INSTRUCTION_FILES = [
    ".sagewai/instructions.md",
    ".sagewai/AGENTS.md",
]

# Markers that indicate project root (stop walking here)
ROOT_MARKERS = [
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
]

# Default token budget for merged instructions
DEFAULT_TOKEN_BUDGET = 12_000
CHARS_PER_TOKEN = 4  # conservative estimate


class InstructionFileLoader:
    """Loads instruction files from directory ancestors.

    Parameters
    ----------
    instruction_files:
        File paths (relative to each directory) to look for.
        Defaults to [".sagewai/instructions.md", ".sagewai/AGENTS.md"].
    root_markers:
        Directory markers that indicate project root (stop walking).
    token_budget:
        Maximum tokens for merged instruction content.
    stop_at_root:
        If True, stop at the first project root marker found.
        If False, walk all the way to filesystem root.
    """

    def __init__(
        self,
        *,
        instruction_files: list[str] | None = None,
        root_markers: list[str] | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        stop_at_root: bool = True,
    ) -> None:
        self.instruction_files = instruction_files or list(INSTRUCTION_FILES)
        self.root_markers = root_markers or list(ROOT_MARKERS)
        self.token_budget = token_budget
        self.stop_at_root = stop_at_root

    def load(self, start_dir: str | Path | None = None) -> str:
        """Load and merge instruction files from directory ancestors.

        Walks from start_dir upward, collecting instruction content.
        Returns merged content (root-first, specific overrides).

        Args:
            start_dir: Starting directory. Defaults to cwd.

        Returns:
            Merged instruction content, or empty string if none found.
        """
        start = Path(start_dir) if start_dir else Path.cwd()
        start = start.resolve()

        # Collect files from each ancestor, grouped by directory level
        # (bottom-up walk, reversed later to get root-first order).
        dir_groups: list[list[tuple[Path, str]]] = []

        current = start
        while True:
            level_files: list[tuple[Path, str]] = []
            for rel_path in self.instruction_files:
                file_path = current / rel_path
                if file_path.is_file():
                    try:
                        content = file_path.read_text(
                            encoding="utf-8"
                        ).strip()
                        if content:
                            level_files.append((file_path, content))
                            logger.debug(
                                "Found instruction file: %s", file_path
                            )
                    except (OSError, UnicodeDecodeError):
                        logger.warning(
                            "Failed to read instruction file: %s",
                            file_path,
                        )
            if level_files:
                dir_groups.append(level_files)

            # Check for root marker
            if self.stop_at_root and self._is_root(current):
                break

            parent = current.parent
            if parent == current:
                break  # filesystem root
            current = parent

        if not dir_groups:
            return ""

        # Reverse groups to get root-first order (we collected bottom-up),
        # preserving file order within each directory level.
        dir_groups.reverse()
        found_files: list[tuple[Path, str]] = [
            entry for group in dir_groups for entry in group
        ]

        # Dedup by content hash
        seen_hashes: set[str] = set()
        unique_contents: list[str] = []
        for _path, content in found_files:
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            if content_hash not in seen_hashes:
                seen_hashes.add(content_hash)
                unique_contents.append(content)

        # Merge with separators
        merged = "\n\n---\n\n".join(unique_contents)

        # Truncate to token budget
        max_chars = self.token_budget * CHARS_PER_TOKEN
        if len(merged) > max_chars:
            original_len = len(merged)
            merged = merged[:max_chars].rsplit("\n", 1)[0]
            merged += (
                "\n\n[... instructions truncated to fit token budget]"
            )
            logger.info(
                "Truncated instructions from %d to %d chars"
                " (budget: %d tokens)",
                original_len,
                len(merged),
                self.token_budget,
            )

        return merged

    def _is_root(self, directory: Path) -> bool:
        """Check if directory contains a project root marker."""
        for marker in self.root_markers:
            if (directory / marker).exists():
                return True
        return False
