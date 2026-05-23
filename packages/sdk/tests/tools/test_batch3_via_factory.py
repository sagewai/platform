# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end batch-3 tests through ``build_callables``.

Each test seeds an authorized OAuth client in a tmp vault, mocks the
vendor REST endpoint with ``respx``, and runs the catalogued tool via
``factory.build_callables`` exactly as the autopilot would.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from sagewai.oauth import vault
from sagewai.sealed.crypto import Crypto
from sagewai.tools import factory, registry
from sagewai.tools.executors import http as http_executor


@pytest.fixture
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "store.json"
    monkeypatch.setattr(vault, "_store_path", lambda: path)
    monkeypatch.setattr(http_executor, "_resolve_store_path", lambda: path)
    return path


@pytest.fixture
def crypto(monkeypatch: pytest.MonkeyPatch) -> Crypto:
    key = Fernet.generate_key()
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key.decode())
    crypto = Crypto(key)
    monkeypatch.setattr(http_executor, "_resolve_crypto", lambda: crypto)
    return crypto


def _seed_client(
    store_path: Path,
    crypto: Crypto,
    *,
    provider: str,
    granted_scopes: list[str],
    project_id: str = "default",
) -> str:
    rec = vault.create_client(
        store_path, crypto,
        provider=provider, project_id=project_id, display_name=f"{provider} test",
        client_id="CID", client_secret="SEC",
        redirect_uri="http://localhost/cb",
        requested_scopes=list(granted_scopes),
    )
    now = datetime.now(timezone.utc)
    vault.update_tokens(
        store_path, crypto, rec["id"],
        tokens={
            "access_token": "AT-LIVE",
            "refresh_token": "RT-LIVE",
            "token_type": "Bearer",
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "obtained_at": now.isoformat(),
            "last_refreshed_at": None,
        },
        granted_scopes=list(granted_scopes),
        status="authorized",
    )
    return rec["id"]


@pytest.fixture(autouse=True)
def _reload_catalog():
    registry._reset()
    registry.load()
    yield


# ── Spotify ─────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_spotify_search_via_factory(store_path, crypto):
    _seed_client(store_path, crypto, provider="spotify",
                 granted_scopes=["user-read-private", "playlist-read-private",
                                 "playlist-modify-private", "playlist-modify-public",
                                 "user-read-currently-playing", "user-top-read"])
    route = respx.get("https://api.spotify.com/v1/search").mock(
        return_value=httpx.Response(200, json={"tracks": {"items": []}}),
    )
    callables = factory.build_callables(
        project_id="default",
        get_credentials=lambda **kw: {},  # oauth2 doesn't use creds
    )
    result = await callables["spotify"]({"_operation": "search", "q": "deadmau5", "type": "track"})
    assert result == {"tracks": {"items": []}}
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer AT-LIVE"
    assert req.url.params["q"] == "deadmau5"
    assert req.url.params["type"] == "track"


@respx.mock
@pytest.mark.asyncio
async def test_spotify_get_track_path_param(store_path, crypto):
    _seed_client(store_path, crypto, provider="spotify",
                 granted_scopes=["user-read-private", "playlist-read-private",
                                 "playlist-modify-private", "playlist-modify-public",
                                 "user-read-currently-playing", "user-top-read"])
    route = respx.get("https://api.spotify.com/v1/tracks/abc123").mock(
        return_value=httpx.Response(200, json={"id": "abc123", "name": "Strobe"}),
    )
    callables = factory.build_callables(project_id="default", get_credentials=lambda **kw: {})
    result = await callables["spotify"]({"_operation": "get_track", "id": "abc123"})
    assert result["id"] == "abc123"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_spotify_create_playlist_json_body(store_path, crypto):
    _seed_client(store_path, crypto, provider="spotify",
                 granted_scopes=["user-read-private", "playlist-read-private",
                                 "playlist-modify-private", "playlist-modify-public",
                                 "user-read-currently-playing", "user-top-read"])
    route = respx.post("https://api.spotify.com/v1/users/me/playlists").mock(
        return_value=httpx.Response(201, json={"id": "pl-1", "name": "Late Night"}),
    )
    callables = factory.build_callables(project_id="default", get_credentials=lambda **kw: {})
    result = await callables["spotify"]({
        "_operation": "create_playlist",
        "user_id": "me",
        "name": "Late Night",
        "public": False,
    })
    assert result["id"] == "pl-1"
    assert route.called
    body = route.calls.last.request.read()
    assert b'"name": "Late Night"' in body or b'"name":"Late Night"' in body


# ── Gmail ────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_gmail_search_messages_via_factory(store_path, crypto):
    _seed_client(store_path, crypto, provider="google",
                 granted_scopes=["https://www.googleapis.com/auth/gmail.modify",
                                 "https://www.googleapis.com/auth/gmail.send"])
    route = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]}),
    )
    callables = factory.build_callables(project_id="default", get_credentials=lambda **kw: {})
    result = await callables["gmail"]({"_operation": "search_messages", "q": "subject:hello"})
    assert len(result["messages"]) == 2
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer AT-LIVE"
    assert req.url.params["q"] == "subject:hello"


@respx.mock
@pytest.mark.asyncio
async def test_gmail_send_message_json_body(store_path, crypto):
    _seed_client(store_path, crypto, provider="google",
                 granted_scopes=["https://www.googleapis.com/auth/gmail.modify",
                                 "https://www.googleapis.com/auth/gmail.send"])
    route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send").mock(
        return_value=httpx.Response(200, json={"id": "sent-1"}),
    )
    raw_msg = base64.urlsafe_b64encode(b"To: x@y.com\r\nSubject: hi\r\n\r\nbody").decode()
    callables = factory.build_callables(project_id="default", get_credentials=lambda **kw: {})
    result = await callables["gmail"]({"_operation": "send_message", "raw": raw_msg})
    assert result["id"] == "sent-1"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_gmail_list_labels(store_path, crypto):
    _seed_client(store_path, crypto, provider="google",
                 granted_scopes=["https://www.googleapis.com/auth/gmail.modify",
                                 "https://www.googleapis.com/auth/gmail.send"])
    route = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/labels").mock(
        return_value=httpx.Response(200, json={"labels": [{"id": "INBOX"}, {"id": "SENT"}]}),
    )
    callables = factory.build_callables(project_id="default", get_credentials=lambda **kw: {})
    result = await callables["gmail"]({"_operation": "list_labels"})
    assert len(result["labels"]) == 2
    assert route.called
