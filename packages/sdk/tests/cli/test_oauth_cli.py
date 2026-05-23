# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for `sagewai oauth` CLI command group.

Uses the same vault.store_path-monkeypatch + SAGEWAI_MASTER_KEY env-var
pattern as the admin route tests. The interactive ``add``/``reauthorize``
flow's loopback listener is mocked by overriding ``_run_loopback_callback``
(the seam the production code calls into) so tests run instantly without
opening real sockets or browsers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from click.testing import CliRunner
from cryptography.fernet import Fernet
from httpx import Response

from sagewai.cli.oauth import oauth as oauth_cmd
from sagewai.oauth import vault
from sagewai.sealed.crypto import Crypto


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def master_key(monkeypatch) -> bytes:
    """Set a stable Fernet master key for Sealed.Crypto."""
    key = Fernet.generate_key()
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key.decode("ascii"))
    return key


@pytest.fixture()
def store_path(tmp_path: Path, monkeypatch) -> Path:
    """Sandbox the vault file under tmp_path."""
    p = tmp_path / "store.json"
    monkeypatch.setattr(vault, "_store_path", lambda: p)
    return p


@pytest.fixture()
def crypto(master_key: bytes) -> Crypto:
    return Crypto(master_key)


# ── Helpers ─────────────────────────────────────────────────────────


def _seed_spotify(store_path: Path, crypto: Crypto, project: str = "default") -> str:
    created = vault.create_client(
        store_path,
        crypto,
        provider="spotify",
        project_id=project,
        display_name="Spotify",
        client_id="spot-id",
        client_secret="spot-secret",
        redirect_uri="http://127.0.0.1:53999/callback",
        requested_scopes=["user-read-private"],
    )
    cid = created["id"]
    vault.update_tokens(
        store_path,
        crypto,
        cid,
        tokens={
            "access_token": "spot-access-OLD",
            "refresh_token": "spot-refresh-OLD",
            "token_type": "Bearer",
            "expires_at": "2026-01-01T00:00:00Z",
        },
        granted_scopes=["user-read-private"],
        status="authorized",
    )
    return cid


def _seed_google(store_path: Path, crypto: Crypto, project: str = "default") -> str:
    created = vault.create_client(
        store_path,
        crypto,
        provider="google",
        project_id=project,
        display_name="Google",
        client_id="google-id",
        client_secret="google-secret",
        redirect_uri="http://127.0.0.1:53999/callback",
        requested_scopes=["openid", "email"],
    )
    cid = created["id"]
    vault.update_tokens(
        store_path,
        crypto,
        cid,
        tokens={
            "access_token": "google-access",
            "refresh_token": "google-refresh",
            "token_type": "Bearer",
            "expires_at": "2026-01-01T00:00:00Z",
        },
        granted_scopes=["openid", "email"],
        status="authorized",
    )
    return cid


# ── Tests ───────────────────────────────────────────────────────────


def test_providers_lists_both(master_key, store_path):
    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["providers"])
    assert result.exit_code == 0, result.output
    assert "spotify" in result.output
    assert "google" in result.output


def test_list_shows_clients_per_project(master_key, store_path, crypto):
    _seed_spotify(store_path, crypto, project="default")

    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["list", "--project", "default"])
    assert result.exit_code == 0, result.output
    assert "Spotify" in result.output


def test_list_empty_project(master_key, store_path):
    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["list", "--project", "ghost"])
    assert result.exit_code == 0
    # No rows; output should mention something about empty / no clients
    # Without --json, we should at least not crash.


@respx.mock
def test_add_happy_path_writes_record(master_key, store_path, monkeypatch):
    """End-to-end add: monkey-patched loopback returns code+state,
    respx mocks token endpoint, vault gets an authorized record."""

    # Monkeypatch the loopback helper to return an auth code immediately,
    # echoing whatever state the CLI generated.
    def fake_loopback(*, authorize_url, port, state, timeout):
        return ("AUTH-CODE", state)

    monkeypatch.setattr(
        "sagewai.cli.oauth._run_loopback_callback", fake_loopback,
    )
    # Don't try to open a real browser in tests.
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: False)

    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=Response(
            200,
            json={
                "access_token": "spot-access-NEW",
                "refresh_token": "spot-refresh-NEW",
                "expires_in": 3600,
                "scope": "user-read-private",
                "token_type": "Bearer",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        oauth_cmd,
        [
            "add",
            "spotify",
            "--display-name", "Test",
            "--scopes", "user-read-private",
            "--redirect-port", "53999",
        ],
        input="CID\nSEC\n",
    )
    assert result.exit_code == 0, result.output

    # Vault now has one authorized client.
    clients = vault.list_clients(store_path, "default")
    assert len(clients) == 1
    rec = clients[0]
    assert rec["provider"] == "spotify"
    assert rec["status"] == "authorized"
    assert rec["display_name"] == "Test"
    assert "user-read-private" in rec["granted_scopes"]


@respx.mock
def test_refresh_updates_expires_at(master_key, store_path, crypto):
    cid = _seed_spotify(store_path, crypto)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=Response(
            200,
            json={
                "access_token": "spot-access-REFRESHED",
                "refresh_token": "spot-refresh-NEW",
                "expires_in": 7200,
                "scope": "user-read-private",
                "token_type": "Bearer",
            },
        )
    )
    # Capture old expires_at.
    old = vault.get_client(store_path, cid)
    old_exp = old["tokens"]["expires_at"]

    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["refresh", cid])
    assert result.exit_code == 0, result.output

    new = vault.get_client(store_path, cid)
    new_exp = new["tokens"]["expires_at"]
    assert new_exp != old_exp
    assert new["status"] == "authorized"


@respx.mock
def test_revoke_clears_tokens(master_key, store_path, crypto):
    cid = _seed_google(store_path, crypto)
    revoke_route = respx.post("https://oauth2.googleapis.com/revoke").mock(
        return_value=Response(200, json={})
    )

    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["revoke", cid, "--yes"])
    assert result.exit_code == 0, result.output
    assert revoke_route.called

    rec = vault.get_client(store_path, cid)
    assert rec is not None
    assert rec["status"] == "revoked"
    assert rec["tokens"] is None


def test_json_flag_returns_valid_json(master_key, store_path, crypto):
    _seed_spotify(store_path, crypto)
    runner = CliRunner()

    # `list --json` should be parseable JSON
    result = runner.invoke(oauth_cmd, ["list", "--project", "default", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1

    # `providers --json` likewise
    result = runner.invoke(oauth_cmd, ["providers", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    ids = {p["id"] for p in parsed}
    assert "spotify" in ids
    assert "google" in ids


def test_status_returns_record(master_key, store_path, crypto):
    cid = _seed_spotify(store_path, crypto)
    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["status", cid])
    assert result.exit_code == 0, result.output
    assert "spotify" in result.output


def test_status_missing_record_nonzero(master_key, store_path):
    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["status", "oauth_spotify_doesnotexist"])
    assert result.exit_code != 0


def test_delete_with_yes_removes_record(master_key, store_path, crypto):
    cid = _seed_spotify(store_path, crypto)
    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["delete", cid, "--yes"])
    assert result.exit_code == 0, result.output
    assert vault.get_client(store_path, cid) is None


def test_set_default_marks_target(master_key, store_path, crypto):
    cid1 = _seed_spotify(store_path, crypto)
    # Add a second spotify client in same project.
    created2 = vault.create_client(
        store_path,
        crypto,
        provider="spotify",
        project_id="default",
        display_name="Spotify Backup",
        client_id="spot-id-2",
        client_secret="spot-secret-2",
        redirect_uri="http://127.0.0.1:53999/callback",
        requested_scopes=["user-read-private"],
    )
    cid2 = created2["id"]

    runner = CliRunner()
    result = runner.invoke(oauth_cmd, ["set-default", cid2])
    assert result.exit_code == 0, result.output

    rec1 = vault.get_client(store_path, cid1)
    rec2 = vault.get_client(store_path, cid2)
    assert rec1["is_default"] is False
    assert rec2["is_default"] is True
