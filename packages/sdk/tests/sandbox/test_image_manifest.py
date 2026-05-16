# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the auto-generated image_manifest module."""
import re

from sagewai.sandbox.image_manifest import (
    PINNED_DIGESTS,
    SDK_VERSION,
    TOOL_RUNNER_VERSION_SPEC,
    lookup_digest,
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def test_sdk_version_is_a_string():
    assert isinstance(SDK_VERSION, str)
    assert SDK_VERSION  # non-empty — may be "0.0.0-dev" before first release


def test_tool_runner_version_spec_shape():
    # Expected format like ">=0.1,<0.2"
    assert ">=" in TOOL_RUNNER_VERSION_SPEC
    assert "<" in TOOL_RUNNER_VERSION_SPEC


def test_pinned_digests_is_a_dict():
    assert isinstance(PINNED_DIGESTS, dict)


def test_pinned_digests_values_match_sha256():
    for variant, digest in PINNED_DIGESTS.items():
        assert _SHA256_RE.match(digest), (
            f"{variant}: {digest!r} is not a valid sha256:<64hex> digest"
        )


def test_lookup_digest_known_variant():
    # Seed an entry to ensure lookup works regardless of release state.
    if "base" in PINNED_DIGESTS:
        assert lookup_digest("ghcr.io/sagewai/sandbox-base:0.1.0") == PINNED_DIGESTS["base"]


def test_lookup_digest_unknown_image_returns_none():
    assert lookup_digest("ghcr.io/acme/custom:1.0") is None


def test_lookup_digest_non_sagewai_org_returns_none():
    assert lookup_digest("ghcr.io/sagewai/sandbox-base-fork:0.1.0") is None
