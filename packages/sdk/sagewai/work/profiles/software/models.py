# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Typed context and workspace models for the software Work profile."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class SoftwareContractContext(BaseModel):
    """Software-specific immutable contract state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str


class SoftwareCapsuleContext(BaseModel):
    """Software-specific context compiled for an operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    current_sha: str
    repo_instructions: tuple[str, ...]
    verification_commands: tuple[str, ...]


class SoftwareAttemptContext(BaseModel):
    """Software-specific execution receipt state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    result_sha: str | None


class SoftwareWorkspace(BaseModel):
    """One isolated worktree pinned to an attempt's base revision."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    ref: str
    project_id: str
    work_id: str
    attempt_id: str
    repository: Path
    path: Path
    base_sha: str
    initial_sha: str


class WorkspaceStaleError(RuntimeError):
    """The pinned workspace no longer has the expected Git state."""
