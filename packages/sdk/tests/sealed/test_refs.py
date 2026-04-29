# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ProfileRef + backend registry."""

import pytest

from sagewai.sealed.backend import (
    ProfileNotFoundError,
)
from sagewai.sealed.refs import (
    ProfileRef,
    UnknownBackendError,
    list_registered_schemes,
    register_backend,
    resolve_backend,
)


def test_parse_bare_id():
    r = ProfileRef.parse("acme-prod")
    assert r.scheme == "builtin"
    assert r.path == "acme-prod"


def test_parse_with_scheme():
    r = ProfileRef.parse("vault://kv/sagewai/acme-prod")
    assert r.scheme == "vault"
    assert r.path == "kv/sagewai/acme-prod"


def test_parse_one_password_scheme():
    r = ProfileRef.parse("1password://Sagewai/acme-prod")
    # leading digit allowed in scheme per RFC 3986? actually no — must start with alpha.
    # Validate that '1password' is rejected → bare-ID fallback OR error.
    # Decision: reject as scheme; treat as bare-id fallback.
    assert r.scheme == "builtin"   # whole string is a bare ID since "1password" isn't a valid scheme
    assert r.path == "1password://Sagewai/acme-prod"


def test_str_round_trip():
    r = ProfileRef(scheme="vault", path="kv/x")
    assert str(r) == "vault://kv/x"


def test_unknown_backend_raises():
    r = ProfileRef.parse("vault://kv/x")
    with pytest.raises(UnknownBackendError):
        resolve_backend(r)


def test_register_and_resolve():
    class FakeBackend:
        name = "fake"
        scheme = "fake"
        async def list_profiles(self): return []
        async def get_profile_metadata(self, pid): raise ProfileNotFoundError(pid)
        async def get_profile(self, pid): raise ProfileNotFoundError(pid)
        async def save_profile(self, p): raise NotImplementedError
        async def delete_profile(self, pid): raise NotImplementedError
        async def supports_master_key_rotation(self): return False
        async def rotate_master_key(self, k): raise NotImplementedError

    fb = FakeBackend()
    register_backend(fb)
    try:
        resolved = resolve_backend(ProfileRef.parse("fake://anything"))
        assert resolved is fb
        assert "fake" in list_registered_schemes()
    finally:
        # Clean up registry to avoid pollution
        from sagewai.sealed.refs import _BACKENDS
        _BACKENDS.pop("fake", None)


def test_register_backend_idempotent_same_object():
    """Re-registering the same backend instance is a no-op."""
    class FakeBackend:
        name = "idem"
        scheme = "idem"
        async def list_profiles(self): return []
        async def get_profile_metadata(self, pid): raise ProfileNotFoundError(pid)
        async def get_profile(self, pid): raise ProfileNotFoundError(pid)
        async def save_profile(self, p): raise NotImplementedError
        async def delete_profile(self, pid): raise NotImplementedError
        async def supports_master_key_rotation(self): return False
        async def rotate_master_key(self, k): raise NotImplementedError

    fb = FakeBackend()
    register_backend(fb)
    register_backend(fb)  # second call — no error
    try:
        assert resolve_backend(ProfileRef.parse("idem://x")) is fb
    finally:
        from sagewai.sealed.refs import _BACKENDS
        _BACKENDS.pop("idem", None)


def test_register_backend_rejects_scheme_collision():
    """A different backend claiming an already-registered scheme is rejected."""
    class A:
        name = "a"
        scheme = "collide"
        async def list_profiles(self): return []
        async def get_profile_metadata(self, pid): raise ProfileNotFoundError(pid)
        async def get_profile(self, pid): raise ProfileNotFoundError(pid)
        async def save_profile(self, p): raise NotImplementedError
        async def delete_profile(self, pid): raise NotImplementedError
        async def supports_master_key_rotation(self): return False
        async def rotate_master_key(self, k): raise NotImplementedError

    class B:
        name = "b"
        scheme = "collide"
        async def list_profiles(self): return []
        async def get_profile_metadata(self, pid): raise ProfileNotFoundError(pid)
        async def get_profile(self, pid): raise ProfileNotFoundError(pid)
        async def save_profile(self, p): raise NotImplementedError
        async def delete_profile(self, pid): raise NotImplementedError
        async def supports_master_key_rotation(self): return False
        async def rotate_master_key(self, k): raise NotImplementedError

    a = A()
    b = B()
    register_backend(a)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_backend(b)
    finally:
        from sagewai.sealed.refs import _BACKENDS
        _BACKENDS.pop("collide", None)


class TestDefaultScheme:
    def setup_method(self):
        # Reset to canonical default before each case
        from sagewai.sealed.refs import BUILTIN_SCHEME, set_default_scheme
        set_default_scheme(BUILTIN_SCHEME)

    def teardown_method(self):
        from sagewai.sealed.refs import BUILTIN_SCHEME, set_default_scheme
        set_default_scheme(BUILTIN_SCHEME)

    def test_bare_id_defaults_to_builtin_by_default(self):
        from sagewai.sealed.refs import ProfileRef
        ref = ProfileRef.parse("acme-prod")
        assert ref.scheme == "builtin"
        assert ref.path == "acme-prod"

    def test_set_default_scheme_changes_bare_id_dispatch(self):
        from sagewai.sealed.refs import ProfileRef, set_default_scheme
        set_default_scheme("vault")
        ref = ProfileRef.parse("acme-prod")
        assert ref.scheme == "vault"
        assert ref.path == "acme-prod"

    def test_uri_form_unaffected_by_default_scheme(self):
        from sagewai.sealed.refs import ProfileRef, set_default_scheme
        set_default_scheme("vault")
        ref = ProfileRef.parse("builtin://acme-prod")
        assert ref.scheme == "builtin"
        assert ref.path == "acme-prod"

    def test_set_default_scheme_validates_scheme_format(self):
        from sagewai.sealed.refs import set_default_scheme
        with pytest.raises(ValueError):
            set_default_scheme("Invalid Scheme")
        with pytest.raises(ValueError):
            set_default_scheme("")
