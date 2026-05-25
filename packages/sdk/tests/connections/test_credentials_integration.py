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


import httpx
import respx


@respx.mock
def test_doppler_round_trip_with_realistic_oauth2_record():
    """Full encrypt+decrypt cycle through CredentialsBackendRouter with doppler."""
    config = {
        "kind": "doppler",
        "config": {
            "service_token": "dp.st.dev.x",
            "project": "sagewai", "config": "prd",
            "name_prefix": "SPOTIFY_MARKETING",
        },
    }
    respx.get("https://api.doppler.com/v3/configs/config/secrets").mock(
        return_value=httpx.Response(200, json={"secrets": {
            "SPOTIFY_MARKETING_CLIENT_SECRET": {"computed": "csec-real"},
            "SPOTIFY_MARKETING_TOKENS_ACCESS_TOKEN": {"computed": "AT-real"},
            "SPOTIFY_MARKETING_TOKENS_REFRESH_TOKEN": {"computed": "RT-real"},
        }}),
    )
    router = CredentialsBackendRouter()
    data = {
        "client_secret": "csec-real",
        "tokens": {
            "access_token": "AT-real",
            "refresh_token": "RT-real",
            "token_type": "Bearer",
        },
    }
    paths = ("client_secret", "tokens.access_token", "tokens.refresh_token")
    encrypted = router.encrypt(
        data, sensitive_field_paths=paths,
        connection_credentials_backend=config,
    )
    # Markers only, no plaintext
    assert encrypted["client_secret"] == {"$doppler": {"name": "SPOTIFY_MARKETING_CLIENT_SECRET"}}
    import json as _json
    assert "csec-real" not in _json.dumps(encrypted)
    # Round-trip
    decrypted = router.decrypt(
        encrypted, sensitive_field_paths=paths,
        connection_credentials_backend=config,
    )
    assert decrypted["client_secret"] == "csec-real"
    assert decrypted["tokens"]["access_token"] == "AT-real"


def test_vault_round_trip_with_realistic_oauth2_record(monkeypatch):
    """Full encrypt+decrypt cycle through router with vault (stubbed hvac)."""

    # Build a fake hvac module
    class _FakeKvV2:
        def read_secret_version(self, *, path, mount_point):
            return {"data": {"data": {
                "client_secret": "csec",
                "access_token": "AT",
                "refresh_token": "RT",
            }}}

    class _FakeClient:
        def __init__(self, **kw):
            self.token = None
            self.secrets = type("S", (), {"kv": type("K", (), {"v2": _FakeKvV2()})})()

    class _FakeHvacModule:
        Client = _FakeClient

    monkeypatch.setattr(
        "sagewai.connections.credentials.vault._lazy_import_hvac",
        lambda: _FakeHvacModule,
    )

    config = {
        "kind": "vault",
        "config": {
            "url": "https://vault.x",
            "base_path": "sagewai/spotify",
            "auth": {"mode": "token", "token": "hvs.stub"},
        },
    }
    router = CredentialsBackendRouter()
    data = {
        "client_secret": "csec",
        "tokens": {"access_token": "AT", "refresh_token": "RT", "token_type": "Bearer"},
    }
    paths = ("client_secret", "tokens.access_token", "tokens.refresh_token")
    encrypted = router.encrypt(
        data, sensitive_field_paths=paths,
        connection_credentials_backend=config,
    )
    assert encrypted["client_secret"] == {"$vault": {
        "path": "sagewai/spotify", "key": "client_secret",
    }}
    decrypted = router.decrypt(
        encrypted, sensitive_field_paths=paths,
        connection_credentials_backend=config,
    )
    assert decrypted["client_secret"] == "csec"


def test_swap_local_to_doppler(monkeypatch):
    """Local-encrypted record → swap → Doppler markers."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())

    router = CredentialsBackendRouter()
    data = {"client_secret": "real-secret"}
    paths = ("client_secret",)
    # Encrypt with local
    local_encrypted = router.encrypt(
        data, sensitive_field_paths=paths,
        connection_credentials_backend=None,
    )
    assert local_encrypted["client_secret"].startswith("fernet:")
    # Swap to doppler (only requires marker write — no HTTP)
    doppler_config = {
        "kind": "doppler",
        "config": {
            "service_token": "dp.st.dev.x",
            "project": "p", "config": "c", "name_prefix": "PFX",
        },
    }
    swapped = router.swap(
        local_encrypted,
        sensitive_field_paths=paths,
        old_credentials_backend=None,
        new_credentials_backend=doppler_config,
    )
    assert swapped["client_secret"] == {"$doppler": {"name": "PFX_CLIENT_SECRET"}}


def test_top_level_re_exports_resolve():
    """sagewai.connections re-exports the credentials surface."""
    from sagewai.connections import (
        CREDENTIALS_BACKENDS,
        CredentialsBackend,
        CredentialsBackendRouter,
        CredentialsError,
        UnknownCredentialsBackendError,
    )
    assert len(CREDENTIALS_BACKENDS) == 5
    assert issubclass(UnknownCredentialsBackendError, CredentialsError)
    r = CredentialsBackendRouter()  # constructable
    backend, _ = r.get_backend_for(None)
    assert isinstance(backend, CredentialsBackend)
