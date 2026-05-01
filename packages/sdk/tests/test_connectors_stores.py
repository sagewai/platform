# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
# packages/sagewai/tests/test_connectors_stores.py
import pytest
import asyncio
from sagewai.connectors.stores import (
    CredentialStore,
    OAuthTokenStore,
    CursorStore,
    InMemoryCredentialStore,
    InMemoryOAuthTokenStore,
    InMemoryCursorStore,
)
from sagewai.connectors.base import TokenSet, ConnectorStatus


@pytest.fixture
def cred_store():
    return InMemoryCredentialStore()


@pytest.fixture
def token_store():
    return InMemoryOAuthTokenStore()


@pytest.fixture
def cursor_store():
    return InMemoryCursorStore()


@pytest.mark.asyncio
async def test_cred_store_put_get(cred_store):
    await cred_store.put("slack", {"bot_token": "xoxb-123"})
    result = await cred_store.get("slack")
    assert result == {"bot_token": "xoxb-123"}


@pytest.mark.asyncio
async def test_cred_store_get_missing(cred_store):
    result = await cred_store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cred_store_delete(cred_store):
    await cred_store.put("slack", {"bot_token": "xoxb-123"})
    await cred_store.delete("slack")
    assert await cred_store.get("slack") is None


@pytest.mark.asyncio
async def test_cred_store_list_all(cred_store):
    await cred_store.put("slack", {"bot_token": "xoxb-123"})
    await cred_store.put("email", {"api_key": "SG.abc"})
    items = await cred_store.list_all()
    assert len(items) == 2
    names = {s.connector_name for s in items}
    assert names == {"slack", "email"}


@pytest.mark.asyncio
async def test_token_store_save_get(token_store):
    ts = TokenSet(access_token="access-abc", refresh_token="refresh-xyz")
    await token_store.save_token("x", ts)
    result = await token_store.get_token("x")
    assert result is not None
    assert result.access_token == "access-abc"


@pytest.mark.asyncio
async def test_token_store_needs_refresh_no_token(token_store):
    assert await token_store.needs_refresh("nonexistent") is True


@pytest.mark.asyncio
async def test_token_store_needs_refresh_no_expiry(token_store):
    ts = TokenSet(access_token="abc")
    await token_store.save_token("x", ts)
    assert await token_store.needs_refresh("x") is False


@pytest.mark.asyncio
async def test_cursor_store_set_get(cursor_store):
    await cursor_store.set("slack", "#general", "ts-12345")
    result = await cursor_store.get("slack", "#general")
    assert result == "ts-12345"


@pytest.mark.asyncio
async def test_cursor_store_get_missing(cursor_store):
    result = await cursor_store.get("slack", "#unknown")
    assert result is None
