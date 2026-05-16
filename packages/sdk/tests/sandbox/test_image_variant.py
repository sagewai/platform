# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for image_manifest.lookup_variant."""
from sagewai.sandbox import image_manifest
from sagewai.sandbox.image_manifest import lookup_variant
from sagewai.sandbox.models import SandboxImageVariant


def test_lookup_variant_known_in_manifest(monkeypatch):
    monkeypatch.setitem(image_manifest.PINNED_DIGESTS, "ml", "sha256:" + "0" * 64)
    assert lookup_variant("ghcr.io/sagewai/sandbox-ml:0.1.5") is SandboxImageVariant.ML


def test_lookup_variant_known_enum_but_not_in_manifest(monkeypatch):
    """Enum knows 'ops' but current SDK hasn't pinned it."""
    monkeypatch.setattr(image_manifest, "PINNED_DIGESTS", {"base": "sha256:" + "0" * 64})
    assert lookup_variant("ghcr.io/sagewai/sandbox-ops:0.1.5") is None


def test_lookup_variant_byo_other_registry():
    assert lookup_variant("ghcr.io/acme/custom-sandbox:1.0") is None


def test_lookup_variant_byo_non_sagewai_name():
    assert lookup_variant("ghcr.io/sagewai/other-thing:1.0") is None


def test_lookup_variant_empty_manifest(monkeypatch):
    monkeypatch.setattr(image_manifest, "PINNED_DIGESTS", {})
    assert lookup_variant("ghcr.io/sagewai/sandbox-base:0.1.5") is None


def test_lookup_variant_digest_form_ref():
    """Digest-form refs (@sha256:...) outside this helper's scope."""
    ref = "ghcr.io/sagewai/sandbox-base@sha256:" + "0" * 64
    assert lookup_variant(ref) is None


def test_lookup_variant_malformed():
    assert lookup_variant("not a valid ref") is None
    assert lookup_variant("") is None
