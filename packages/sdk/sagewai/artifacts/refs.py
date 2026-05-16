# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Uploader registry — type literal → ArtifactUploader instance.

Each uploader module registers itself at import time. The runtime hook
(sagewai.artifacts.runtime) calls ``resolve_uploader`` to get the
implementation for a destination's type.
"""
from __future__ import annotations

from sagewai.artifacts.models import (
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
)
from sagewai.artifacts.uploader import ArtifactUploader

_UPLOADERS: dict[ArtifactDestinationType, ArtifactUploader] = {}


def register_uploader(uploader: ArtifactUploader) -> None:
    """Register ``uploader`` under its type. Replaces any prior registration."""
    _UPLOADERS[uploader.type] = uploader


def resolve_uploader(type: ArtifactDestinationType) -> ArtifactUploader:
    """Return the uploader registered for ``type``.

    Raises ``ArtifactDestinationConfigError`` if no uploader is registered.
    """
    uploader = _UPLOADERS.get(type)
    if uploader is None:
        raise ArtifactDestinationConfigError(
            f"no uploader registered for destination type {type.value!r}",
        )
    return uploader


def registered_types() -> list[ArtifactDestinationType]:
    """Return the list of currently registered destination types."""
    return list(_UPLOADERS.keys())
