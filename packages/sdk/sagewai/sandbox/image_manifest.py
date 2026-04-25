# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Auto-generated image manifest — DO NOT EDIT BY HAND.

Maps each Sagewai-published sandbox variant to the exact multi-arch image
digest that the `release-sandbox.yml` workflow produced for this SDK version.
Rewritten at release time by the `manifest-and-commit` job.

DockerBackend reads this module on startup. It enforces strict digest
matching for known ghcr.io/sagewai/sandbox-* refs; unknown refs (BYO or
local :dev builds) skip the check with a single INFO log line.
"""
from __future__ import annotations

import re

from sagewai.sandbox.models import SandboxImageVariant

# Version of the SDK wheel this manifest was generated into. Before the
# first release this reads "0.0.0-dev" and PINNED_DIGESTS is empty.
SDK_VERSION: str = "0.0.0-dev"

# Accepted tool-runner protocol versions for images in this manifest.
# Must be a PEP 440 version specifier string consumable by
# packaging.specifiers.SpecifierSet.
TOOL_RUNNER_VERSION_SPEC: str = ">=0.1,<0.2"

# Variant name → multi-arch manifest digest. Populated by release workflow.
PINNED_DIGESTS: dict[str, str] = {}


_IMAGE_REF_RE = re.compile(
    r"^ghcr\.io/sagewai/sandbox-(?P<variant>[a-z][a-z0-9-]*):[^@]+$"
)


def lookup_digest(image_ref: str) -> str | None:
    """Return the pinned digest for ``image_ref`` or None if it is BYO.

    Matches only tag-form refs under the sagewai org (e.g. ``ghcr.io/sagewai/
    sandbox-base:0.1.5``). Digest-form refs (``@sha256:...``) are treated as
    caller-provided pins and are outside this helper's scope — callers that
    pass digest-form refs should skip the lookup and trust the ref directly.
    """
    match = _IMAGE_REF_RE.match(image_ref)
    if match is None:
        return None
    variant = match.group("variant")
    return PINNED_DIGESTS.get(variant)


def lookup_variant(image_ref: str) -> SandboxImageVariant | None:
    """Return the variant for a known Sagewai-published image, or None for BYO.

    Matches only tag-form refs under the sagewai org (e.g.
    ``ghcr.io/sagewai/sandbox-base:0.1.5``). Digest-form refs (``@sha256:...``)
    are treated as outside scope and return None — callers that pass
    digest-form refs should skip the lookup and trust the ref directly.

    Returns None when:
      - ref does not start with ``ghcr.io/sagewai/sandbox-``
      - the variant segment is not a known SandboxImageVariant enum value
      - the variant IS a known enum value but not present in the current
        SDK's PINNED_DIGESTS (pre-release state or partial release)
    """
    prefix = "ghcr.io/sagewai/sandbox-"
    if not image_ref.startswith(prefix):
        return None
    rest = image_ref[len(prefix):]
    # Require tag-form: variant + ':' + tag
    if ":" not in rest:
        return None
    variant_name = rest.split(":", 1)[0]
    try:
        variant = SandboxImageVariant(variant_name)
    except ValueError:
        return None
    if variant.value not in PINNED_DIGESTS:
        return None
    return variant
