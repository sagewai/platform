# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SOPS backend tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sagewai.connections.credentials.errors import (
    InvalidBackendConfigError,
    SopsDecryptError,
)
from sagewai.connections.credentials.sops import SopsBackend


@pytest.fixture
def sops_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_SOPS_ROOT", str(tmp_path))
    return tmp_path


def test_identity():
    b = SopsBackend()
    assert b.id == "sops"
    assert b.display_name == "Mozilla SOPS"


def test_encrypt_replaces_leaves_with_sops_marker(sops_root):
    b = SopsBackend()
    data = {"client_secret": "real-secret"}
    config = {"file": "spotify.sops.yaml", "key": "client_secret"}
    encrypted = b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    assert encrypted["client_secret"] == {
        "$sops": {"file": "spotify.sops.yaml", "key": "client_secret"}
    }


def test_encrypt_does_not_write_to_sops_file(sops_root):
    """Platform does not touch the SOPS file at encrypt time — operator manages it."""
    b = SopsBackend()
    data = {"client_secret": "VERY_SECRET"}
    config = {"file": "spotify.sops.yaml", "key": "client_secret"}
    b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    # No file created
    assert not (sops_root / "spotify.sops.yaml").exists()


def test_decrypt_shells_out_to_sops(sops_root, monkeypatch):
    """Mock subprocess.run to return a fake SOPS decryption output."""
    b = SopsBackend()
    sops_file = sops_root / "spotify.sops.yaml"
    sops_file.write_text("# encrypted contents (mocked)")

    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout=b"client_secret: real-spotify-secret\nother: x\n",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    data = {"client_secret": {"$sops": {"file": "spotify.sops.yaml", "key": "client_secret"}}}
    config = {"file": "spotify.sops.yaml", "key": "client_secret"}
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    assert decrypted["client_secret"] == "real-spotify-secret"
    assert len(calls) == 1
    assert calls[0][0] == "sops"
    assert "--decrypt" in calls[0]
    assert str(sops_file) in calls[0]


def test_decrypt_with_nested_key_path(sops_root, monkeypatch):
    """When backend_config has `key_path`, walk dotted path inside the YAML."""
    b = SopsBackend()
    (sops_root / "x.sops.yaml").write_text("# mocked")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout=b"tokens:\n  refresh: rt-value\n",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    data = {"refresh_token": {"$sops": {
        "file": "x.sops.yaml", "key": "tokens.refresh"
    }}}
    config = {"file": "x.sops.yaml", "key": "tokens.refresh"}
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("refresh_token",), backend_config=config
    )
    assert decrypted["refresh_token"] == "rt-value"


def test_decrypt_subprocess_failure_raises(sops_root, monkeypatch):
    b = SopsBackend()
    (sops_root / "x.sops.yaml").write_text("# mocked")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=2, stdout=b"", stderr=b"sops: failed to decrypt"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    data = {"client_secret": {"$sops": {"file": "x.sops.yaml", "key": "client_secret"}}}
    config = {"file": "x.sops.yaml", "key": "client_secret"}
    with pytest.raises(SopsDecryptError, match="failed to decrypt"):
        b.decrypt_fields(
            data, sensitive_field_paths=("client_secret",), backend_config=config
        )


def test_decrypt_missing_file_raises(sops_root):
    b = SopsBackend()
    # File doesn't exist
    data = {"client_secret": {"$sops": {"file": "nope.sops.yaml", "key": "client_secret"}}}
    config = {"file": "nope.sops.yaml", "key": "client_secret"}
    with pytest.raises(SopsDecryptError, match="not found"):
        b.decrypt_fields(
            data, sensitive_field_paths=("client_secret",), backend_config=config
        )


def test_decrypt_rejects_path_outside_sops_root(sops_root, monkeypatch):
    """Path traversal defense — file paths must resolve inside SAGEWAI_SOPS_ROOT."""
    b = SopsBackend()
    data = {"client_secret": {"$sops": {"file": "../../etc/passwd", "key": "client_secret"}}}
    config = {"file": "../../etc/passwd", "key": "client_secret"}
    with pytest.raises(SopsDecryptError, match="outside"):
        b.decrypt_fields(
            data, sensitive_field_paths=("client_secret",), backend_config=config
        )


def test_health_ok_when_sops_binary_and_file_present(sops_root, monkeypatch):
    b = SopsBackend()
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/local/bin/sops" if binary == "sops" else None)
    (sops_root / "x.sops.yaml").write_text("# mocked")
    config = {"file": "x.sops.yaml", "key": "client_secret"}
    result = b.health(backend_config=config)
    assert result.ok is True


def test_health_not_ok_when_sops_binary_missing(sops_root, monkeypatch):
    b = SopsBackend()
    monkeypatch.setattr("shutil.which", lambda binary: None)
    (sops_root / "x.sops.yaml").write_text("# mocked")
    config = {"file": "x.sops.yaml", "key": "client_secret"}
    result = b.health(backend_config=config)
    assert result.ok is False
    assert "sops binary" in (result.message or "").lower()


def test_health_not_ok_when_file_missing(sops_root, monkeypatch):
    b = SopsBackend()
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/local/bin/sops")
    config = {"file": "nope.sops.yaml", "key": "client_secret"}
    result = b.health(backend_config=config)
    assert result.ok is False
    assert "not found" in (result.message or "").lower()


def test_validate_config_requires_file_and_key():
    b = SopsBackend()
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({})
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({"file": "x"})  # no key
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({"key": "x"})   # no file


def test_validate_config_accepts_optional_key_path():
    b = SopsBackend()
    b.validate_config({"file": "x.sops.yaml", "key": "client_secret"})  # OK
    b.validate_config({
        "file": "x.sops.yaml", "key": "client_secret", "key_path": "tokens.refresh",
    })  # also OK
