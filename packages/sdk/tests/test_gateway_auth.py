# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for gateway auth dependency."""

from __future__ import annotations

import pytest

from sagewai.gateway.auth import GatewayAuthConfig, gateway_auth
from sagewai.gateway.manager import TokenManager
from sagewai.gateway.store import InMemoryTokenStore


@pytest.fixture
def token_manager():
    return TokenManager(store=InMemoryTokenStore())


@pytest.fixture
def config(token_manager):
    return GatewayAuthConfig(
        jwt_secret="test-secret",
        api_keys=["sk-sage-testkey123"],
        token_manager=token_manager,
    )


@pytest.mark.asyncio
async def test_jwt_auth(config):
    from sagewai.auth.jwt import JWTAuth

    jwt = JWTAuth(secret="test-secret")
    token = jwt.create_token({"sub": "user-1", "role": "admin"})
    auth = gateway_auth(config)
    result = await auth(authorization=f"Bearer {token}", api_key="")
    assert result["sub"] == "user-1"
    assert result["auth_type"] == "jwt"


@pytest.mark.asyncio
async def test_api_key_auth(config):
    auth = gateway_auth(config)
    result = await auth(authorization="", api_key="sk-sage-testkey123")
    assert result["auth_type"] == "api_key"


@pytest.mark.asyncio
async def test_access_token_auth(config, token_manager):
    token = await token_manager.generate(agent_name="scout", grantor_id="admin-1")
    auth = gateway_auth(config)
    result = await auth(authorization=f"Bearer {token}", api_key="")
    assert result["auth_type"] == "access_token"
    assert result["agent_name"] == "scout"
    assert result["grantor_id"] == "admin-1"


@pytest.mark.asyncio
async def test_no_credentials_raises(config):
    from sagewai.auth.jwt import AuthenticationError

    auth = gateway_auth(config)
    with pytest.raises(AuthenticationError):
        await auth(authorization="", api_key="")


@pytest.mark.asyncio
async def test_access_token_scope_in_payload(config, token_manager):
    token = await token_manager.generate(
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat", "dream"],
    )
    auth = gateway_auth(config)
    result = await auth(authorization=f"Bearer {token}", api_key="")
    assert result["scopes"] == ["chat", "dream"]


@pytest.mark.asyncio
async def test_jwt_takes_priority_over_access_token(config, token_manager):
    """JWT is tried first; if valid, access token check is skipped."""
    from sagewai.auth.jwt import JWTAuth

    jwt = JWTAuth(secret="test-secret")
    jwt_token = jwt.create_token({"sub": "user-1"})
    auth = gateway_auth(config)
    result = await auth(authorization=f"Bearer {jwt_token}", api_key="")
    assert result["auth_type"] == "jwt"
