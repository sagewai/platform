# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""FastAPI authentication middleware — route protection via dependency injection.

Provides ``require_auth`` dependency for FastAPI routes that validates
JWT tokens or API keys from request headers.

Usage::

    from fastapi import FastAPI, Depends
    from sagewai.auth import AuthConfig, require_auth

    config = AuthConfig(jwt_secret="my-secret", api_keys=["sk-sage-abc123"])
    auth = require_auth(config)

    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth)])
    async def protected():
        return {"status": "authenticated"}

    @app.get("/user")
    async def user_info(payload: dict = Depends(auth)):
        return {"user": payload.get("sub")}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AuthConfig:
    """Authentication configuration for FastAPI middleware.

    Parameters
    ----------
    jwt_secret:
        Secret for JWT verification. If None, JWT auth is disabled.
    jwt_algorithm:
        JWT signing algorithm (default: HS256).
    jwt_issuer:
        Expected JWT issuer claim.
    jwt_audience:
        Expected JWT audience claim.
    api_keys:
        List of valid API keys. If empty, API key auth is disabled.
    header_name:
        HTTP header name for the auth token (default: Authorization).
    api_key_header:
        HTTP header name for API keys (default: X-API-Key).
    """

    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    api_keys: list[str] = field(default_factory=list)
    header_name: str = "Authorization"
    api_key_header: str = "X-API-Key"


def require_auth(config: AuthConfig):
    """Create a FastAPI dependency for route authentication.

    Supports both JWT Bearer tokens and API keys.
    JWT is checked first via the Authorization header,
    then API key via X-API-Key header.

    Args:
        config: Authentication configuration.

    Returns:
        FastAPI dependency callable.
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
        """FastAPI dependency that validates auth credentials.

        Can be used with FastAPI's Request object or as a standalone validator.
        """
        # Try to extract from kwargs (when called directly for testing)
        authorization = kwargs.get("authorization", "")
        api_key = kwargs.get("api_key", "")

        # Try JWT auth first
        if jwt_auth and authorization:
            token = authorization
            if token.startswith("Bearer "):
                token = token[7:]
            try:
                return jwt_auth.verify_token(token)
            except AuthenticationError:
                pass

        # Try API key auth
        if api_key_auth and api_key:
            if api_key_auth.is_valid(api_key):
                return {"auth_type": "api_key", "key_valid": True}

        raise AuthenticationError("Authentication required. Provide a valid JWT token or API key.")

    return _auth_dependency
