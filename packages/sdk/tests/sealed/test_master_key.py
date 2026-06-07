# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for master-key resolution chain."""
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from sagewai.sealed.master_key import (
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    MasterKeyMissing,
    default_key_path,
    resolve_master_key,
    store_master_key,
)


def test_resolve_from_env_var(monkeypatch):
    fake_key = Fernet.generate_key()
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", fake_key.decode("ascii"))
    key, source = resolve_master_key()
    assert key == fake_key
    assert source == "env-var"


def test_resolve_from_keychain(monkeypatch):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    fake_key = Fernet.generate_key()

    class _FakeKeyring:
        @staticmethod
        def get_password(service, user):
            if service == KEYRING_SERVICE and user == KEYRING_USERNAME:
                return fake_key.decode("ascii")
            return None

    with patch("sagewai.sealed.master_key.keyring", _FakeKeyring()):
        # Make file path not exist for this test
        monkeypatch.setattr(
            "sagewai.sealed.master_key.default_key_path",
            lambda: Path("/tmp/nonexistent-sagewai-key-test"),
        )
        key, source = resolve_master_key()
    assert key == fake_key
    assert source == "keychain"


def test_resolve_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setattr("sagewai.sealed.master_key.keyring", None)
    fake_key = Fernet.generate_key()
    key_path = tmp_path / "master.key"
    key_path.write_text(fake_key.decode("ascii"))
    key_path.chmod(0o600)
    monkeypatch.setattr("sagewai.sealed.master_key.default_key_path", lambda: key_path)

    key, source = resolve_master_key()
    assert key == fake_key
    assert source == "file"


def test_file_loose_perms_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setattr("sagewai.sealed.master_key.keyring", None)
    fake_key = Fernet.generate_key()
    key_path = tmp_path / "master.key"
    key_path.write_text(fake_key.decode("ascii"))
    key_path.chmod(0o644)  # group + other readable
    monkeypatch.setattr("sagewai.sealed.master_key.default_key_path", lambda: key_path)

    with pytest.raises(MasterKeyMissing, match="insecure permissions"):
        resolve_master_key()


def test_nothing_configured_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setattr("sagewai.sealed.master_key.keyring", None)
    monkeypatch.setattr("sagewai.sealed.master_key.default_key_path", lambda: tmp_path / "nope.key")

    with pytest.raises(MasterKeyMissing, match="No SAGEWAI_MASTER_KEY"):
        resolve_master_key()


def test_invalid_key_length_raises(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "too-short")
    with pytest.raises(MasterKeyMissing, match="must be 44 chars"):
        resolve_master_key()


def test_store_master_key_to_file(tmp_path):
    fake_key = Fernet.generate_key()
    target = tmp_path / "master.key"
    store_master_key(fake_key, "file", path=target)
    assert target.read_bytes().strip() == fake_key
    mode = target.stat().st_mode
    assert mode & 0o777 == 0o600


def test_keychain_invalid_key_length_propagates(monkeypatch):
    """Wrong-length keychain entry must NOT be silently swallowed."""
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)

    class _BadKeyring:
        @staticmethod
        def get_password(service, user):
            return "too-short"  # 9 chars, not 44

    monkeypatch.setattr("sagewai.sealed.master_key.keyring", _BadKeyring())
    monkeypatch.setattr(
        "sagewai.sealed.master_key.default_key_path",
        lambda: Path("/tmp/nonexistent-sagewai-key-test-bad-keychain"),
    )
    with pytest.raises(MasterKeyMissing, match="must be 44 chars"):
        resolve_master_key()


def test_keychain_backend_error_falls_through_to_file(tmp_path, monkeypatch):
    """A genuine keyring backend error (e.g. locked) falls through cleanly."""
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    fake_key = Fernet.generate_key()

    class _ErroringKeyring:
        @staticmethod
        def get_password(service, user):
            raise RuntimeError("keychain locked")

    monkeypatch.setattr("sagewai.sealed.master_key.keyring", _ErroringKeyring())
    key_path = tmp_path / "master.key"
    key_path.write_text(fake_key.decode("ascii"))
    key_path.chmod(0o600)
    monkeypatch.setattr("sagewai.sealed.master_key.default_key_path", lambda: key_path)

    key, source = resolve_master_key()
    assert key == fake_key
    assert source == "file"


def test_store_master_key_rejects_invalid_length(tmp_path):
    """store_master_key must validate key length before writing."""
    target = tmp_path / "master.key"
    with pytest.raises(ValueError, match="must be 44 chars"):
        store_master_key(b"too-short", "file", path=target)
    assert not target.exists()  # no partial write
