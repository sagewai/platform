# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ProfileBackend.supports_value_history / get_secret_at_version."""
import pytest
from sagewai.sealed.backend import (
    BackendUnsupportedOperationError,
    ProfileBackend,
)
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend


async def test_builtin_supports_value_history_false(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "0" * 44)
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
    )
    assert await backend.supports_value_history() is False


async def test_builtin_get_secret_at_version_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "0" * 44)
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
    )
    with pytest.raises(BackendUnsupportedOperationError):
        await backend.get_secret_at_version("p", "k", "v1")


def test_protocol_runtime_check_includes_history_methods():
    """ProfileBackend Protocol declares the new methods."""
    assert hasattr(ProfileBackend, "supports_value_history")
    assert hasattr(ProfileBackend, "get_secret_at_version")
