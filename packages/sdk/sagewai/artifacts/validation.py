# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Per-type target validators + env_keys allowlist check for Plan ART."""
from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
)

_GITHUB_HTTPS = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?/?$",
)
_GITHUB_SSH = re.compile(r"^git@github\.com:[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+\.git$")
_S3_TARGET = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,62}(/[A-Za-z0-9_.\-/]+[A-Za-z0-9_.\-])?$")


def validate_target(type: ArtifactDestinationType, target: str) -> None:
    """Per-type structural check.

    Raises ArtifactDestinationConfigError on invalid input.
    """
    if not target:
        raise ArtifactDestinationConfigError(
            f"target is required for type {type.value!r}",
        )

    if type is ArtifactDestinationType.GITHUB:
        if not (_GITHUB_HTTPS.match(target) or _GITHUB_SSH.match(target)):
            raise ArtifactDestinationConfigError(
                f"github target must be a github.com URL "
                f"(https://github.com/<org>/<repo>(.git)? or git@github.com:<org>/<repo>.git): "
                f"got {target!r}",
            )
        # Extra defence: parse https form to catch malformed URLs
        if target.startswith("https://"):
            parsed = urlparse(target)
            if parsed.netloc != "github.com":
                raise ArtifactDestinationConfigError(
                    f"github target host must be github.com, got {parsed.netloc!r}",
                )
        return

    if type is ArtifactDestinationType.S3:
        if "://" in target or target.startswith("/"):
            raise ArtifactDestinationConfigError(
                f"s3 target must be 'bucket' or 'bucket/prefix' (no scheme, no leading slash): "
                f"got {target!r}",
            )
        if target.endswith("/"):
            raise ArtifactDestinationConfigError(
                f"s3 target must not end with '/': got {target!r}",
            )
        if not _S3_TARGET.match(target):
            raise ArtifactDestinationConfigError(
                f"s3 target must match [bucket]/[optional/prefix] with valid characters: "
                f"got {target!r}",
            )
        return

    if type is ArtifactDestinationType.LOCAL:
        if not target.startswith("/"):
            raise ArtifactDestinationConfigError(
                f"local target must be an absolute path: got {target!r}",
            )
        return

    raise ArtifactDestinationConfigError(f"unknown destination type: {type!r}")


def validate_env_keys_subset(
    env_keys: Iterable[str],
    effective_secret_keys: Iterable[str],
) -> None:
    """Raise if any env_keys are not present in effective_secret_keys."""
    requested = set(env_keys)
    available = set(effective_secret_keys)
    missing = requested - available
    if missing:
        raise ArtifactDestinationConfigError(
            f"artifact destination references env keys not in the resolved Sealed cascade: "
            f"missing={sorted(missing)}; available={sorted(available)}",
        )


def validate_destination(
    destination: ArtifactDestination,
    effective_secret_keys: Iterable[str],
) -> None:
    """Run per-type target validation then env_keys allowlist check."""
    validate_target(destination.type, destination.target)
    validate_env_keys_subset(destination.env_keys, effective_secret_keys)
