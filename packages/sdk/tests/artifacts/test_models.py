# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sagewai.artifacts.models — Plan ART."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.artifacts.models import (
    ArtifactBackendUnsupportedError,
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationError,
    ArtifactDestinationType,
    ArtifactUploadError,
    ArtifactUploadResult,
)
from sagewai.errors import SagewaiError


def test_artifact_destination_type_values():
    assert ArtifactDestinationType.GITHUB.value == "github"
    assert ArtifactDestinationType.S3.value == "s3"
    assert ArtifactDestinationType.LOCAL.value == "local"
    assert {t.value for t in ArtifactDestinationType} == {"github", "s3", "local"}


def test_artifact_destination_round_trip_github():
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
        options={"branch": "main"},
    )
    dumped = dest.model_dump(mode="json")
    rebuilt = ArtifactDestination.model_validate(dumped)
    assert rebuilt == dest


def test_artifact_destination_defaults():
    dest = ArtifactDestination(type=ArtifactDestinationType.S3, target="bucket/prefix")
    assert dest.env_keys == []
    assert dest.options == {}


def test_artifact_destination_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ArtifactDestination.model_validate(
            {"type": "ftp", "target": "ftp://example.com/path"},
        )


def test_artifact_destination_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ArtifactDestination.model_validate(
            {
                "type": "github",
                "target": "https://github.com/acme/x.git",
                "extra_field": "not allowed",
            },
        )


def test_artifact_upload_result_defaults():
    result = ArtifactUploadResult(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        bytes_uploaded=1024,
        duration_ms=42,
    )
    assert result.ref is None
    assert result.object_count is None
    assert result.warnings == []


def test_error_hierarchy():
    assert issubclass(ArtifactDestinationError, SagewaiError)
    assert issubclass(ArtifactDestinationConfigError, ArtifactDestinationError)
    assert issubclass(ArtifactUploadError, ArtifactDestinationError)
    assert issubclass(ArtifactBackendUnsupportedError, ArtifactDestinationError)
