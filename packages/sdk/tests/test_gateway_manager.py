# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for gateway token manager."""

from __future__ import annotations

import pytest

from sagewai.gateway.manager import TokenManager
from sagewai.gateway.models import TokenStatus
from sagewai.gateway.store import InMemoryTokenStore


@pytest.fixture
def manager():
    store = InMemoryTokenStore()
    return TokenManager(store=store)


@pytest.mark.asyncio
async def test_generate_returns_token_string(manager):
    token = await manager.generate(agent_name="scout", grantor_id="admin-1")
    assert token.startswith("sat-")
    assert len(token) > 20


@pytest.mark.asyncio
async def test_generate_stores_hash_not_plaintext(manager):
    token = await manager.generate(agent_name="scout", grantor_id="admin-1")
    tokens = await manager.store.list_tokens()
    assert len(tokens) == 1
    assert tokens[0].token_hash != token
    assert tokens[0].agent_name == "scout"


@pytest.mark.asyncio
async def test_validate_valid_token(manager):
    token = await manager.generate(agent_name="scout", grantor_id="admin-1")
    access = await manager.validate(token)
    assert access is not None
    assert access.agent_name == "scout"
    assert access.status == TokenStatus.ACTIVE


@pytest.mark.asyncio
async def test_validate_invalid_token(manager):
    access = await manager.validate("sat-nonexistent")
    assert access is None


@pytest.mark.asyncio
async def test_validate_expired_token(manager):
    token = await manager.generate(
        agent_name="scout",
        grantor_id="admin-1",
        expires_in_seconds=0,
    )
    import asyncio

    await asyncio.sleep(0.01)
    access = await manager.validate(token)
    assert access is None


@pytest.mark.asyncio
async def test_validate_revoked_token(manager):
    token = await manager.generate(agent_name="scout", grantor_id="admin-1")
    tokens = await manager.store.list_tokens()
    await manager.revoke(tokens[0].token_id)
    access = await manager.validate(token)
    assert access is None


@pytest.mark.asyncio
async def test_single_use_token(manager):
    token = await manager.generate(
        agent_name="scout",
        grantor_id="admin-1",
        single_use=True,
    )
    first = await manager.validate(token)
    assert first is not None
    second = await manager.validate(token)
    assert second is None


@pytest.mark.asyncio
async def test_generate_with_scopes(manager):
    token = await manager.generate(
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat", "dream"],
    )
    access = await manager.validate(token)
    assert access is not None
    assert access.scopes == ["chat", "dream"]


@pytest.mark.asyncio
async def test_list_tokens(manager):
    await manager.generate(agent_name="scout", grantor_id="admin-1")
    await manager.generate(agent_name="writer", grantor_id="admin-1")
    all_tokens = await manager.list_tokens()
    assert len(all_tokens) == 2
    scout_tokens = await manager.list_tokens(agent_name="scout")
    assert len(scout_tokens) == 1
