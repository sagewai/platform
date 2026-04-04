# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""JWT token management — creation, verification, and refresh.

Uses PyJWT (``pyjwt``) for token encoding/decoding with HS256 by default.
Supports configurable expiration, issuer, and audience claims.

Requires ``pyjwt`` (optional dependency)::

    uv add pyjwt
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ALGORITHM = "HS256"
_DEFAULT_EXPIRY_SECONDS = 3600  # 1 hour


class JWTAuth:
    """JWT token manager for creating and verifying tokens.

    Parameters
    ----------
    secret:
        Secret key for signing tokens.
    algorithm:
        Signing algorithm (default: HS256).
    expiry_seconds:
        Default token expiration in seconds (default: 3600).
    issuer:
        Optional issuer claim (``iss``).
    audience:
        Optional audience claim (``aud``).
    """

    def __init__(
        self,
        *,
        secret: str,
        algorithm: str = _DEFAULT_ALGORITHM,
        expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        if not secret:
            raise ValueError("JWT secret must not be empty")
        self._secret = secret
        self._algorithm = algorithm
        self._expiry_seconds = expiry_seconds
        self._issuer = issuer
        self._audience = audience

    def create_token(
        self,
        payload: dict[str, Any],
        *,
        expiry_seconds: int | None = None,
    ) -> str:
        """Create a signed JWT token.

        Args:
            payload: Claims to include in the token.
            expiry_seconds: Override default expiration.

        Returns:
            Encoded JWT string.
        """
        jwt = _import_jwt()

        now = int(time.time())
        exp = expiry_seconds if expiry_seconds is not None else self._expiry_seconds

        claims = {
            **payload,
            "iat": now,
            "exp": now + exp,
        }
        if self._issuer:
            claims["iss"] = self._issuer
        if self._audience:
            claims["aud"] = self._audience

        return jwt.encode(claims, self._secret, algorithm=self._algorithm)

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT token.

        Args:
            token: Encoded JWT string.

        Returns:
            Decoded payload dict.

        Raises:
            AuthenticationError: If the token is invalid or expired.
        """
        jwt = _import_jwt()

        try:
            options: dict[str, Any] = {}
            kwargs: dict[str, Any] = {
                "algorithms": [self._algorithm],
            }
            if self._issuer:
                kwargs["issuer"] = self._issuer
            if self._audience:
                kwargs["audience"] = self._audience
            else:
                options["verify_aud"] = False

            return jwt.decode(
                token,
                self._secret,
                options=options,
                **kwargs,
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid token: {exc}") from exc

    def refresh_token(self, token: str, *, expiry_seconds: int | None = None) -> str:
        """Refresh a token by verifying it and issuing a new one.

        Args:
            token: Current valid token.
            expiry_seconds: Override default expiration for new token.

        Returns:
            New encoded JWT string.
        """
        payload = self.verify_token(token)
        # Remove time-related claims so they get regenerated
        for key in ("iat", "exp", "nbf"):
            payload.pop(key, None)
        return self.create_token(payload, expiry_seconds=expiry_seconds)


class AuthenticationError(Exception):
    """Raised when authentication fails."""


def _import_jwt():
    """Lazy import of PyJWT."""
    try:
        import jwt

        return jwt
    except ImportError as exc:
        raise ImportError(
            "pyjwt is required for JWT authentication. Install it with: uv add pyjwt"
        ) from exc
