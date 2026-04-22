# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Mode precedence and image resolution helpers."""
from __future__ import annotations

from sagewai.sandbox.models import SandboxConfig, SandboxMode

_MODE_RANK: dict[SandboxMode, int] = {
    SandboxMode.NONE: 0,
    SandboxMode.PER_TOOL: 1,
    SandboxMode.PER_RUN: 2,
    SandboxMode.PER_WORKER: 3,
}

_ENVIRONMENT_DEFAULTS: dict[str, SandboxMode] = {
    "production": SandboxMode.PER_RUN,
    "staging": SandboxMode.PER_TOOL,
    "development": SandboxMode.NONE,
}

_HARD_DEFAULT_IMAGE = "ghcr.io/sagewai/sandbox-general:latest"


def mode_rank(mode: SandboxMode | str) -> int:
    """Return integer rank for a sandbox mode. Higher = stronger isolation."""
    if isinstance(mode, str):
        mode = SandboxMode(mode)
    return _MODE_RANK[mode]


def resolve_mode(
    cli_flag: SandboxMode | None,
    config: SandboxConfig,
    project_environment: str | None,
) -> SandboxMode:
    """Resolve the effective mode via precedence.

    Precedence (highest first):
    1. cli_flag
    2. config.mode
    3. project_environment default (production/staging/development)
    4. Hard default: SandboxMode.NONE
    """
    if cli_flag is not None:
        return cli_flag
    if config.mode is not None:
        return config.mode
    if project_environment and project_environment in _ENVIRONMENT_DEFAULTS:
        return _ENVIRONMENT_DEFAULTS[project_environment]
    return SandboxMode.NONE


def resolve_sandbox_image(
    run_image: str | None,
    agent_image: str | None,
    project_image: str | None,
    worker_default: str | None,
) -> str:
    """Resolve sandbox image via precedence: run → agent → project → worker default → hard default."""
    for candidate in (run_image, agent_image, project_image, worker_default):
        if candidate:
            return candidate
    return _HARD_DEFAULT_IMAGE
