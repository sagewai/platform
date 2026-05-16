# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.artifacts.validation — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
)
from sagewai.artifacts.validation import (
    validate_destination,
    validate_env_keys_subset,
    validate_target,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/portfolio.git",
        "https://github.com/acme/portfolio",
        "git@github.com:acme/portfolio.git",
    ],
)
def test_github_target_accepts_valid(url: str) -> None:
    validate_target(ArtifactDestinationType.GITHUB, url)


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "https://example.com/foo/bar",
        "https://gitlab.com/acme/portfolio.git",
        "",
    ],
)
def test_github_target_rejects_invalid(url: str) -> None:
    with pytest.raises(ArtifactDestinationConfigError):
        validate_target(ArtifactDestinationType.GITHUB, url)


@pytest.mark.parametrize(
    "target",
    [
        "my-bucket",
        "my-bucket/path/to/prefix",
        "bucket123/sub.dir",
    ],
)
def test_s3_target_accepts_valid(target: str) -> None:
    validate_target(ArtifactDestinationType.S3, target)


@pytest.mark.parametrize(
    "target",
    [
        "",
        "s3://bucket/foo",
        "/leading-slash",
        "bucket/",
    ],
)
def test_s3_target_rejects_invalid(target: str) -> None:
    with pytest.raises(ArtifactDestinationConfigError):
        validate_target(ArtifactDestinationType.S3, target)


def test_local_target_accepts_absolute() -> None:
    validate_target(ArtifactDestinationType.LOCAL, "/host/output")


@pytest.mark.parametrize("path", ["", "relative/path", "./local"])
def test_local_target_rejects_non_absolute(path: str) -> None:
    with pytest.raises(ArtifactDestinationConfigError):
        validate_target(ArtifactDestinationType.LOCAL, path)


def test_env_keys_subset_passes_when_subset() -> None:
    validate_env_keys_subset(["GITHUB_TOKEN"], {"GITHUB_TOKEN", "OPENAI_API_KEY"})


def test_env_keys_subset_passes_for_empty() -> None:
    validate_env_keys_subset([], {"ANY"})
    validate_env_keys_subset([], set())


def test_env_keys_subset_rejects_missing_keys() -> None:
    with pytest.raises(ArtifactDestinationConfigError) as exc:
        validate_env_keys_subset(
            ["MISSING", "ALSO_MISSING"],
            {"GITHUB_TOKEN"},
        )
    msg = str(exc.value)
    assert "MISSING" in msg
    assert "ALSO_MISSING" in msg


def test_validate_destination_orchestrates_both() -> None:
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
    )
    validate_destination(dest, {"GITHUB_TOKEN"})


def test_validate_destination_rejects_bad_target_first() -> None:
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="not-a-url",
        env_keys=["GITHUB_TOKEN"],
    )
    with pytest.raises(ArtifactDestinationConfigError):
        validate_destination(dest, {"GITHUB_TOKEN"})


def test_validate_destination_rejects_missing_env_keys() -> None:
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        env_keys=["MISSING_KEY"],
    )
    with pytest.raises(ArtifactDestinationConfigError):
        validate_destination(dest, set())
