# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Single-level cascade resolution for artifact destinations.

Run override beats admin override beats code default. No per-key
merge — destinations are atomic values. Mirrors the Sealed-i
'workflow admin override beats code declaration' precedence rule.
"""
from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from sagewai.artifacts.models import ArtifactDestination
from sagewai.artifacts.validation import validate_destination


class ArtifactDestinationLevels(BaseModel):
    """The three input layers for artifact destination resolution."""

    model_config = ConfigDict(extra="forbid")

    code_default: ArtifactDestination | None = None
    admin_override: ArtifactDestination | None = None
    run_override: ArtifactDestination | None = None


def resolve_artifact_destination(
    levels: ArtifactDestinationLevels,
    effective_secret_keys: Iterable[str],
) -> ArtifactDestination | None:
    """Pick the highest-priority non-None destination and validate it.

    Order: run override > admin override > code default.

    Returns None if all three are None. Otherwise validates the picked
    destination's target and env_keys against ``effective_secret_keys``,
    raising ``ArtifactDestinationConfigError`` on failure.
    """
    picked = levels.run_override or levels.admin_override or levels.code_default
    if picked is None:
        return None
    validate_destination(picked, effective_secret_keys)
    return picked
