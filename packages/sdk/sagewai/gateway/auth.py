# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Gateway authentication — extends auth chain with access token validation.

Auth chain priority: JWT → API Key → Access Token.

Usage::

    from sagewai.gateway.auth import GatewayAuthConfig, gateway_auth

    config = GatewayAuthConfig(
        jwt_secret="my-secret",
        api_keys=["sk-sage-abc"],
        token_manager=token_manager,
    )
    auth = gateway_auth(config)

    @app.get("/protected", dependencies=[Depends(auth)])
    async def protected(): ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sagewai.gateway.manager import TokenManager

logger = logging.getLogger(__name__)


@dataclass
class GatewayAuthConfig:
    """Extended auth config with access token support.

    Parameters
    ----------
    jwt_secret:
        Secret for JWT verification. None disables JWT auth.
    jwt_algorithm:
        JWT signing algorithm.
    api_keys:
        Valid API keys. Empty disables API key auth.
    token_manager:
        TokenManager for access token validation. None disables token auth.
    header_name:
        HTTP header for JWT / access tokens.
    api_key_header:
        HTTP header for API keys.
    """

    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    api_keys: list[str] = field(default_factory=list)
    token_manager: TokenManager | None = None
    header_name: str = "Authorization"
    api_key_header: str = "X-API-Key"


def gateway_auth(config: GatewayAuthConfig):
    """Create a FastAPI dependency with JWT + API Key + Access Token auth.

    Auth chain: JWT → API Key → Access Token (``sat-`` prefix).
    Returns a dict payload on success with ``auth_type`` key.
    """
    from sagewai.auth.api_key import APIKeyAuth
    from sagewai.auth.jwt import AuthenticationError, JWTAuth

    jwt_auth: JWTAuth | None = None
    if config.jwt_secret:
        jwt_auth = JWTAuth(
            secret=config.jwt_secret,
            algorithm=config.jwt_algorithm,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )

    api_key_auth: APIKeyAuth | None = None
    if config.api_keys:
        api_key_auth = APIKeyAuth(valid_keys=config.api_keys)

    async def _auth_dependency(**kwargs: Any) -> dict[str, Any]:
        authorization = kwargs.get("authorization", "")
        api_key = kwargs.get("api_key", "")

        bearer_token = ""
        if authorization:
            bearer_token = authorization
            if bearer_token.startswith("Bearer "):
                bearer_token = bearer_token[7:]

        # 1. Try JWT
        if jwt_auth and bearer_token and not bearer_token.startswith("sat-"):
            try:
                payload = jwt_auth.verify_token(bearer_token)
                payload["auth_type"] = "jwt"
                return payload
            except AuthenticationError:
                pass

        # 2. Try API key
        if api_key_auth and api_key:
            if api_key_auth.is_valid(api_key):
                return {"auth_type": "api_key", "key_valid": True}

        # 3. Try access token
        if config.token_manager and bearer_token and bearer_token.startswith("sat-"):
            token = await config.token_manager.validate(bearer_token)
            if token is not None:
                return {
                    "auth_type": "access_token",
                    "token_id": token.token_id,
                    "agent_name": token.agent_name,
                    "grantor_id": token.grantor_id,
                    "scopes": token.scopes,
                }

        raise AuthenticationError(
            "Authentication required. Provide a valid JWT, API key, or access token."
        )

    return _auth_dependency
