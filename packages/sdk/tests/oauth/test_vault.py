# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Vault helpers for ``kind: "oauth_client"`` records.

Tests cover the public API in ``sagewai.oauth.vault``:

- create / list / get roundtrip + masking
- encrypted-at-rest verified by reading the raw JSON file
- ``is_default`` uniqueness per ``(project_id, provider)``
- ``granted_scopes`` stored sorted
- project isolation on list
- ``update_tokens`` then ``get_client_with_secrets`` roundtrip
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sagewai.oauth import vault
from sagewai.sealed.crypto import Crypto


@pytest.fixture()
def crypto() -> Crypto:
    return Crypto(Fernet.generate_key())


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "store.json"


def _make_client(
    store_path: Path,
    crypto: Crypto,
    *,
    project_id: str | None = "default",
    provider: str = "spotify",
    display_name: str = "Spotify",
    client_id: str = "client-id-123",
    client_secret: str = "client-secret-456",
    redirect_uri: str = "http://localhost:8080/cb",
    requested_scopes: list[str] | None = None,
) -> dict:
    return vault.create_client(
        store_path,
        crypto,
        provider=provider,
        project_id=project_id,
        display_name=display_name,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        requested_scopes=requested_scopes or ["user-read-private"],
    )


def test_create_and_list_roundtrip(store_path: Path, crypto: Crypto) -> None:
    created = _make_client(store_path, crypto)
    assert created["provider"] == "spotify"
    assert created["status"] == "pending"
    assert created["is_default"] is True
    # Masking on create return
    assert "client_secret" not in created
    # tokens is None at create time, so no nested secrets to mask
    assert created.get("tokens") is None

    listed = vault.list_clients(store_path, project_id="default")
    assert len(listed) == 1
    row = listed[0]
    assert row["id"] == created["id"]
    assert row["client_id"] == "client-id-123"
    # Masking on list
    assert "client_secret" not in row
    assert row.get("tokens") is None


def test_get_with_secrets_returns_decrypted(
    store_path: Path, crypto: Crypto
) -> None:
    created = _make_client(store_path, crypto, client_secret="super-secret-value")

    full = vault.get_client_with_secrets(store_path, created["id"], crypto)
    assert full is not None
    assert full["client_secret"] == "super-secret-value"

    # Encrypted-at-rest: raw on-disk value must differ from plaintext input.
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    rows = [r for r in raw["providers"] if r.get("kind") == "oauth_client"]
    assert len(rows) == 1
    on_disk_secret = rows[0]["client_secret"]
    assert on_disk_secret != "super-secret-value"
    # Sanity: it carries the Fernet prefix from sagewai.sealed.crypto.
    assert on_disk_secret.startswith("fernet:")


def test_is_default_uniqueness(store_path: Path, crypto: Crypto) -> None:
    first = _make_client(store_path, crypto, display_name="Spotify A")
    assert first["is_default"] is True

    # Second client for same (project, provider) must NOT clobber first's default.
    second = _make_client(store_path, crypto, display_name="Spotify B")
    assert second["is_default"] is False

    # Re-read first; should still be default.
    refreshed_first = vault.get_client(store_path, first["id"])
    assert refreshed_first is not None
    assert refreshed_first["is_default"] is True

    # Flip default to second.
    flipped = vault.set_default(store_path, second["id"])
    assert flipped["is_default"] is True

    # Now invariant: at most one default per (project, provider).
    listed = vault.list_clients(store_path, project_id="default")
    defaults = [r for r in listed if r["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]

    refreshed_first_again = vault.get_client(store_path, first["id"])
    assert refreshed_first_again is not None
    assert refreshed_first_again["is_default"] is False


def test_granted_scopes_stored_sorted(store_path: Path, crypto: Crypto) -> None:
    created = _make_client(
        store_path,
        crypto,
        requested_scopes=["a", "b", "c"],
    )

    tokens = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "token_type": "Bearer",
        "expires_at": "2026-05-23T15:40:00Z",
        "obtained_at": "2026-05-23T14:40:00Z",
        "last_refreshed_at": None,
    }
    updated = vault.update_tokens(
        store_path,
        crypto,
        created["id"],
        tokens=tokens,
        granted_scopes=["b", "a"],
    )
    assert updated["granted_scopes"] == ["a", "b"]

    fetched = vault.get_client(store_path, created["id"])
    assert fetched is not None
    assert fetched["granted_scopes"] == ["a", "b"]


def test_project_isolation(store_path: Path, crypto: Crypto) -> None:
    _make_client(store_path, crypto, project_id="a", display_name="Spotify A")
    _make_client(store_path, crypto, project_id="b", display_name="Spotify B")

    listed_a = vault.list_clients(store_path, project_id="a")
    listed_b = vault.list_clients(store_path, project_id="b")

    assert len(listed_a) == 1
    assert len(listed_b) == 1
    assert listed_a[0]["project_id"] == "a"
    assert listed_b[0]["project_id"] == "b"
    assert listed_a[0]["id"] != listed_b[0]["id"]


def test_update_tokens_then_get(store_path: Path, crypto: Crypto) -> None:
    created = _make_client(
        store_path,
        crypto,
        client_secret="csec-1",
        requested_scopes=["read", "write"],
    )

    tokens = {
        "access_token": "at-plain",
        "refresh_token": "rt-plain",
        "token_type": "Bearer",
        "expires_at": "2026-05-23T15:40:00Z",
        "obtained_at": "2026-05-23T14:40:00Z",
        "last_refreshed_at": None,
    }
    updated = vault.update_tokens(
        store_path,
        crypto,
        created["id"],
        tokens=tokens,
        granted_scopes=["read", "write"],
    )
    # Returned record is masked.
    assert updated["status"] == "authorized"
    assert updated["tokens"] is not None
    assert "access_token" not in updated["tokens"]
    assert "refresh_token" not in updated["tokens"]
    # Non-secret token fields preserved.
    assert updated["tokens"]["token_type"] == "Bearer"
    assert updated["tokens"]["expires_at"] == "2026-05-23T15:40:00Z"

    full = vault.get_client_with_secrets(store_path, created["id"], crypto)
    assert full is not None
    assert full["client_secret"] == "csec-1"
    assert full["tokens"]["access_token"] == "at-plain"
    assert full["tokens"]["refresh_token"] == "rt-plain"
    assert full["tokens"]["token_type"] == "Bearer"

    # And the raw on-disk tokens are encrypted.
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    rows = [r for r in raw["providers"] if r.get("kind") == "oauth_client"]
    on_disk_tokens = rows[0]["tokens"]
    assert on_disk_tokens["access_token"] != "at-plain"
    assert on_disk_tokens["refresh_token"] != "rt-plain"
    assert on_disk_tokens["access_token"].startswith("fernet:")
    assert on_disk_tokens["refresh_token"].startswith("fernet:")
