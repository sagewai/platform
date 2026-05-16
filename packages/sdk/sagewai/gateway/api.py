# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Gateway REST API — token management endpoints.

Usage::

    from sagewai.gateway.api import create_gateway_router
    from sagewai.gateway import TokenManager, InMemoryTokenStore

    manager = TokenManager(store=InMemoryTokenStore())
    app.include_router(create_gateway_router(manager))
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from sagewai.gateway.manager import TokenManager
from sagewai.gateway.models import TokenStatus

logger = logging.getLogger(__name__)


class CreateTokenRequest(BaseModel):
    """Request body for creating an access token."""

    agent_name: str
    grantor_id: str
    scopes: list[str] = Field(default_factory=lambda: ["chat"])
    single_use: bool = False
    expires_in_seconds: int = 86400


class CreateTokenResponse(BaseModel):
    """Response after creating an access token."""

    token: str
    token_id: str
    agent_name: str
    expires_in_seconds: int


class TokenInfo(BaseModel):
    """Token info returned in list responses (no hash exposed)."""

    token_id: str
    agent_name: str
    grantor_id: str
    scopes: list[str]
    status: str
    single_use: bool
    created_at: float
    expires_at: float


def create_gateway_router(manager: TokenManager) -> APIRouter:
    """Create a FastAPI router for token management.

    Args:
        manager: TokenManager instance for token lifecycle.

    Returns:
        FastAPI APIRouter with token CRUD endpoints.
    """
    router = APIRouter(prefix="/gateway", tags=["gateway"])

    @router.post("/tokens", response_model=CreateTokenResponse)
    async def create_token(req: CreateTokenRequest):
        plaintext = await manager.generate(
            agent_name=req.agent_name,
            grantor_id=req.grantor_id,
            scopes=req.scopes,
            single_use=req.single_use,
            expires_in_seconds=req.expires_in_seconds,
        )
        tokens = await manager.list_tokens(agent_name=req.agent_name, limit=1)
        return CreateTokenResponse(
            token=plaintext,
            token_id=tokens[0].token_id,
            agent_name=req.agent_name,
            expires_in_seconds=req.expires_in_seconds,
        )

    @router.get("/tokens", response_model=list[TokenInfo])
    async def list_tokens(agent_name: str | None = None):
        tokens = await manager.list_tokens(agent_name=agent_name)
        return [
            TokenInfo(
                token_id=t.token_id,
                agent_name=t.agent_name,
                grantor_id=t.grantor_id,
                scopes=t.scopes,
                status=t.status.value,
                single_use=t.single_use,
                created_at=t.created_at,
                expires_at=t.expires_at,
            )
            for t in tokens
        ]

    @router.post("/tokens/{token_id}/revoke")
    async def revoke_token(token_id: str):
        await manager.revoke(token_id)
        return {"token_id": token_id, "status": TokenStatus.REVOKED.value}

    @router.delete("/tokens/{token_id}")
    async def delete_token(token_id: str):
        await manager.store.delete(token_id)
        return {"token_id": token_id, "deleted": True}

    return router
