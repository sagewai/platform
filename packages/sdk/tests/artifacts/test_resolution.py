# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.artifacts.resolution — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
)
from sagewai.artifacts.resolution import (
    ArtifactDestinationLevels,
    resolve_artifact_destination,
)


def _dest(type: ArtifactDestinationType, target: str) -> ArtifactDestination:
    return ArtifactDestination(type=type, target=target, env_keys=[])


def test_run_override_beats_admin_override_beats_code_default() -> None:
    code = _dest(ArtifactDestinationType.GITHUB, "https://github.com/acme/code.git")
    admin = _dest(ArtifactDestinationType.GITHUB, "https://github.com/acme/admin.git")
    run = _dest(ArtifactDestinationType.GITHUB, "https://github.com/acme/run.git")

    levels = ArtifactDestinationLevels(
        code_default=code,
        admin_override=admin,
        run_override=run,
    )
    assert resolve_artifact_destination(levels, set()) == run

    levels = ArtifactDestinationLevels(code_default=code, admin_override=admin)
    assert resolve_artifact_destination(levels, set()) == admin

    levels = ArtifactDestinationLevels(code_default=code)
    assert resolve_artifact_destination(levels, set()) == code


def test_all_none_returns_none() -> None:
    levels = ArtifactDestinationLevels()
    assert resolve_artifact_destination(levels, {"GITHUB_TOKEN"}) is None


def test_resolution_validates_env_keys_against_effective_keys() -> None:
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["MISSING_TOKEN"],
    )
    levels = ArtifactDestinationLevels(run_override=dest)
    with pytest.raises(ArtifactDestinationConfigError):
        resolve_artifact_destination(levels, {"GITHUB_TOKEN"})


def test_resolution_passes_resolved_destination_unchanged() -> None:
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        env_keys=[],
        options={"mode": "0644"},
    )
    levels = ArtifactDestinationLevels(code_default=dest)
    resolved = resolve_artifact_destination(levels, set())
    assert resolved is dest  # no copy
