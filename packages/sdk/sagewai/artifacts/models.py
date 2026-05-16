# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pydantic models + error classes for artifact destination resolver."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sagewai.errors import SagewaiError


class ArtifactDestinationType(str, Enum):
    """The three artifact destination types ART ships in v1."""

    GITHUB = "github"
    S3 = "s3"
    LOCAL = "local"


class ArtifactDestination(BaseModel):
    """Where a Mode 3+ workflow's /workspace output lands after the run.

    The actual credential names live in ``env_keys`` and must be a
    subset of the workflow's resolved Sealed ``effective_secret_keys``.
    No credential values appear here — only the names of the env vars
    the upload subprocess reads from the sandbox env.
    """

    model_config = ConfigDict(extra="forbid")

    type: ArtifactDestinationType
    target: str
    env_keys: list[str] = Field(default_factory=list)
    options: dict[str, str] = Field(default_factory=dict)


class ArtifactUploadResult(BaseModel):
    """Returned by ``ArtifactUploader.upload`` and persisted in audit details."""

    model_config = ConfigDict(extra="forbid")

    type: ArtifactDestinationType
    target: str
    bytes_uploaded: int
    duration_ms: int
    ref: str | None = None
    object_count: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ArtifactDestinationError(SagewaiError):
    """Base for all artifact destination errors."""


class ArtifactDestinationConfigError(ArtifactDestinationError):
    """Raised when a destination fails validation (bad target, missing env_keys)."""


class ArtifactUploadError(ArtifactDestinationError):
    """Raised when the upload subprocess returns nonzero or otherwise fails."""


class ArtifactBackendUnsupportedError(ArtifactDestinationError):
    """Raised when the sandbox backend cannot host the requested uploader."""
