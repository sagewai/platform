# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared authentication utilities for Sage apps (API keys, JWT, OAuth).

Provides JWT token management, API key validation, and FastAPI middleware
for route protection across all Sage backend services.

Usage::

    from sagewai.auth import JWTAuth, APIKeyAuth, require_auth

    jwt = JWTAuth(secret="my-secret")
    token = jwt.create_token({"sub": "user-123", "role": "admin"})
    payload = jwt.verify_token(token)

    api_key_auth = APIKeyAuth(valid_keys=["sk-abc123"])
    api_key_auth.validate("sk-abc123")  # True
"""

from sagewai.auth.api_key import APIKeyAuth
from sagewai.auth.jwt import JWTAuth
from sagewai.auth.middleware import AuthConfig, require_auth

__all__ = [
    "APIKeyAuth",
    "AuthConfig",
    "JWTAuth",
    "require_auth",
]
