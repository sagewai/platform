# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: full encrypt/decrypt cycle on a realistic oauth2 record."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials.router import CredentialsBackendRouter
from sagewai.connections.protocols.oauth2 import OAuth2ProtocolPlugin


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    yield


# A realistic authorized oauth2 protocol_data shape
_OAUTH2_DATA = {
    "provider": "spotify",
    "client_id": "spotify-cid",
    "client_secret": "real-spotify-secret",
    "redirect_uri": "http://localhost:8080/cb",
    "requested_scopes": ["user-read-private"],
    "granted_scopes": ["user-read-private"],
    "tokens": {
        "access_token": "AT-real",
        "refresh_token": "RT-real",
        "token_type": "Bearer",
        "expires_at": "2026-05-24T15:00:00+00:00",
        "obtained_at": "2026-05-24T14:00:00+00:00",
        "last_refreshed_at": None,
    },
}


def _sensitive_fields():
    return OAuth2ProtocolPlugin.sensitive_fields


def test_local_round_trip_oauth2():
    r = CredentialsBackendRouter()
    encrypted = r.encrypt(
        _OAUTH2_DATA,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=None,  # local
    )
    # All 3 sensitive paths encrypted
    assert encrypted["client_secret"].startswith("fernet:")
    assert encrypted["tokens"]["access_token"].startswith("fernet:")
    assert encrypted["tokens"]["refresh_token"].startswith("fernet:")
    # Non-sensitive preserved
    assert encrypted["client_id"] == "spotify-cid"
    assert encrypted["tokens"]["token_type"] == "Bearer"
    decrypted = r.decrypt(
        encrypted,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=None,
    )
    assert decrypted == _OAUTH2_DATA


def test_env_round_trip_oauth2(monkeypatch):
    r = CredentialsBackendRouter()
    monkeypatch.setenv("SAGEWAI_TEST_CLIENT_SECRET", "real-spotify-secret")
    monkeypatch.setenv("SAGEWAI_TEST_ACCESS", "AT-real")
    monkeypatch.setenv("SAGEWAI_TEST_REFRESH", "RT-real")
    config = {
        "kind": "env",
        "config": {
            "field_to_env": {
                "client_secret": "SAGEWAI_TEST_CLIENT_SECRET",
                "tokens.access_token": "SAGEWAI_TEST_ACCESS",
                "tokens.refresh_token": "SAGEWAI_TEST_REFRESH",
            },
        },
    }
    encrypted = r.encrypt(
        _OAUTH2_DATA,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=config,
    )
    assert encrypted["client_secret"] == {"$env": "SAGEWAI_TEST_CLIENT_SECRET"}
    assert encrypted["tokens"]["access_token"] == {"$env": "SAGEWAI_TEST_ACCESS"}
    assert encrypted["tokens"]["refresh_token"] == {"$env": "SAGEWAI_TEST_REFRESH"}
    # Sanity: plaintext nowhere in encrypted blob
    import json
    blob = json.dumps(encrypted)
    assert "real-spotify-secret" not in blob
    assert "AT-real" not in blob
    assert "RT-real" not in blob
    decrypted = r.decrypt(
        encrypted,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=config,
    )
    assert decrypted == _OAUTH2_DATA


def test_sops_round_trip_oauth2(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_SOPS_ROOT", str(tmp_path))
    (tmp_path / "spotify.sops.yaml").write_text("# mocked encrypted file")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout=(
                b"client_secret: real-spotify-secret\n"
                b"access_token: AT-real\n"
                b"refresh_token: RT-real\n"
            ),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    # Clear the in-process decryption cache so this test is isolated
    from sagewai.connections.credentials import sops as sops_module
    sops_module._DECRYPT_CACHE.clear()

    r = CredentialsBackendRouter()
    # Per-field SOPS config differs by leaf — encrypt one at a time
    # via three separate calls with three different configs. (Multi-field
    # SOPS is a follow-up enhancement; PR3 SOPS supports one (file, key)
    # per call.)
    cs_config = {"kind": "sops", "config": {"file": "spotify.sops.yaml", "key": "client_secret"}}
    at_config = {"kind": "sops", "config": {"file": "spotify.sops.yaml", "key": "access_token"}}
    rt_config = {"kind": "sops", "config": {"file": "spotify.sops.yaml", "key": "refresh_token"}}

    enc = r.encrypt(_OAUTH2_DATA, sensitive_field_paths=("client_secret",), connection_credentials_backend=cs_config)
    enc = r.encrypt(enc, sensitive_field_paths=("tokens.access_token",), connection_credentials_backend=at_config)
    enc = r.encrypt(enc, sensitive_field_paths=("tokens.refresh_token",), connection_credentials_backend=rt_config)

    assert enc["client_secret"] == {"$sops": {"file": "spotify.sops.yaml", "key": "client_secret"}}
    assert enc["tokens"]["access_token"] == {"$sops": {"file": "spotify.sops.yaml", "key": "access_token"}}
    assert enc["tokens"]["refresh_token"] == {"$sops": {"file": "spotify.sops.yaml", "key": "refresh_token"}}

    dec = r.decrypt(enc, sensitive_field_paths=("client_secret",), connection_credentials_backend=cs_config)
    dec = r.decrypt(dec, sensitive_field_paths=("tokens.access_token",), connection_credentials_backend=at_config)
    dec = r.decrypt(dec, sensitive_field_paths=("tokens.refresh_token",), connection_credentials_backend=rt_config)

    assert dec["client_secret"] == "real-spotify-secret"
    assert dec["tokens"]["access_token"] == "AT-real"
    assert dec["tokens"]["refresh_token"] == "RT-real"


def test_swap_local_to_env_oauth2(monkeypatch):
    monkeypatch.setenv("SAGEWAI_NEW_CSEC", "real-spotify-secret")
    monkeypatch.setenv("SAGEWAI_NEW_AT", "AT-real")
    monkeypatch.setenv("SAGEWAI_NEW_RT", "RT-real")
    r = CredentialsBackendRouter()
    # Start with local-encrypted record
    local = r.encrypt(
        _OAUTH2_DATA,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=None,
    )
    # Swap to env
    new_env = {
        "kind": "env",
        "config": {
            "field_to_env": {
                "client_secret": "SAGEWAI_NEW_CSEC",
                "tokens.access_token": "SAGEWAI_NEW_AT",
                "tokens.refresh_token": "SAGEWAI_NEW_RT",
            },
        },
    }
    swapped = r.swap(
        local,
        sensitive_field_paths=_sensitive_fields(),
        old_credentials_backend=None,
        new_credentials_backend=new_env,
    )
    assert swapped["client_secret"] == {"$env": "SAGEWAI_NEW_CSEC"}
    assert swapped["tokens"]["access_token"] == {"$env": "SAGEWAI_NEW_AT"}
    # End-to-end: decrypt via env → original plaintext
    decrypted = r.decrypt(
        swapped,
        sensitive_field_paths=_sensitive_fields(),
        connection_credentials_backend=new_env,
    )
    assert decrypted == _OAUTH2_DATA


def test_top_level_re_exports_resolve():
    """sagewai.connections re-exports the credentials surface."""
    from sagewai.connections import (
        CREDENTIALS_BACKENDS,
        CredentialsBackend,
        CredentialsBackendRouter,
        CredentialsError,
        UnknownCredentialsBackendError,
    )
    assert len(CREDENTIALS_BACKENDS) == 3
    assert issubclass(UnknownCredentialsBackendError, CredentialsError)
    r = CredentialsBackendRouter()  # constructable
    backend, _ = r.get_backend_for(None)
    assert isinstance(backend, CredentialsBackend)
