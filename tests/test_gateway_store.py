"""Tests for gateway token store."""

from __future__ import annotations

import time

import pytest

from sagewai.gateway.models import AccessToken, TokenStatus
from sagewai.gateway.store import InMemoryTokenStore


@pytest.fixture
def store():
    return InMemoryTokenStore()


@pytest.fixture
def sample_token():
    return AccessToken(
        token_id="tok-123",
        token_hash="abc123hash",
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=time.time() + 3600,
    )


@pytest.mark.asyncio
async def test_save_and_get(store, sample_token):
    await store.save(sample_token)
    result = await store.get("tok-123")
    assert result is not None
    assert result.token_id == "tok-123"
    assert result.agent_name == "scout"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(store):
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_hash(store, sample_token):
    await store.save(sample_token)
    result = await store.get_by_hash("abc123hash")
    assert result is not None
    assert result.token_id == "tok-123"


@pytest.mark.asyncio
async def test_list_by_agent(store, sample_token):
    await store.save(sample_token)
    other = AccessToken(
        token_id="tok-456",
        token_hash="def456hash",
        agent_name="writer",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=time.time() + 3600,
    )
    await store.save(other)
    results = await store.list_tokens(agent_name="scout")
    assert len(results) == 1
    assert results[0].token_id == "tok-123"


@pytest.mark.asyncio
async def test_list_all(store, sample_token):
    await store.save(sample_token)
    results = await store.list_tokens()
    assert len(results) == 1


@pytest.mark.asyncio
async def test_revoke(store, sample_token):
    await store.save(sample_token)
    await store.revoke("tok-123")
    result = await store.get("tok-123")
    assert result is not None
    assert result.status == TokenStatus.REVOKED


@pytest.mark.asyncio
async def test_delete(store, sample_token):
    await store.save(sample_token)
    await store.delete("tok-123")
    result = await store.get("tok-123")
    assert result is None


@pytest.mark.asyncio
async def test_cleanup_expired(store):
    expired = AccessToken(
        token_id="tok-old",
        token_hash="oldhash",
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=time.time() - 100,
    )
    fresh = AccessToken(
        token_id="tok-new",
        token_hash="newhash",
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=time.time() + 3600,
    )
    await store.save(expired)
    await store.save(fresh)
    count = await store.cleanup_expired()
    assert count == 1
    assert await store.get("tok-old") is None
    assert await store.get("tok-new") is not None
