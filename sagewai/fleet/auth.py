# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Worker Registration Token (WRT) — JWT-based auth for fleet workers.

WRT tokens are signed JWTs that fleet workers use to authenticate with
the fleet gateway.  They carry worker identity and scope claims.

Token format::

    wrt-1.<base64_jwt_payload>.<signature>

Standard claims:

- ``sub`` — worker_id
- ``org`` — org_id
- ``pool`` — worker pool name
- ``scopes`` — list of allowed operations (``claim``, ``report``, ``heartbeat``)
- ``jti`` — unique token identifier (for revocation)
- ``iat`` / ``exp`` — standard JWT timestamps

Usage::

    from sagewai.fleet.auth import WRTTokenManager

    mgr = WRTTokenManager(secret="my-secret")
    token = mgr.issue_token(worker_id="w-1", org_id="org-1", pool="gpu")
    claims = mgr.validate_token(token)
    assert claims["sub"] == "w-1"
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WRT_PREFIX = "wrt-1."
_DEFAULT_ALGORITHM = "HS256"
_DEFAULT_EXPIRY_SECONDS = 86400 * 30  # 30 days
_DEFAULT_SCOPES = ["claim", "report", "heartbeat"]


def _import_jwt():
    """Lazy import of PyJWT."""
    try:
        import jwt

        return jwt
    except ImportError as exc:
        raise ImportError(
            "pyjwt is required for WRT token management. "
            "Install it with: uv add pyjwt"
        ) from exc


class WRTTokenManager:
    """Generate, validate, and revoke Worker Registration Tokens.

    Parameters
    ----------
    secret:
        HMAC signing key for tokens.
    default_expiry_seconds:
        Default token lifetime in seconds (default: 30 days).
    revocation_store:
        Optional persistent store for revoked token JTIs.
        Falls back to an in-memory set if not provided.
    """

    def __init__(
        self,
        secret: str,
        default_expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS,
        revocation_store: WRTRevocationStore | None = None,
    ) -> None:
        if not secret:
            raise ValueError("WRT secret must not be empty")
        self._secret = secret
        self._default_expiry = default_expiry_seconds
        self._revocation_store = revocation_store or InMemoryRevocationStore()

    def issue_token(
        self,
        worker_id: str,
        org_id: str,
        pool: str = "default",
        scopes: list[str] | None = None,
        expiry_seconds: int | None = None,
    ) -> str:
        """Issue a new WRT token.

        Args:
            worker_id: Worker identifier (``sub`` claim).
            org_id: Organisation identifier (``org`` claim).
            pool: Worker pool name.
            scopes: Allowed operations.  Defaults to
                ``["claim", "report", "heartbeat"]``.
            expiry_seconds: Override default expiry.

        Returns:
            Full token string with ``wrt-1.`` prefix.
        """
        jwt_mod = _import_jwt()

        now = datetime.now(timezone.utc)
        exp_seconds = (
            expiry_seconds if expiry_seconds is not None else self._default_expiry
        )
        exp = now.timestamp() + exp_seconds

        claims = {
            "sub": worker_id,
            "org": org_id,
            "pool": pool,
            "scopes": scopes if scopes is not None else list(_DEFAULT_SCOPES),
            "jti": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(exp),
        }

        encoded = jwt_mod.encode(claims, self._secret, algorithm=_DEFAULT_ALGORITHM)
        return _WRT_PREFIX + encoded

    def validate_token(self, token: str) -> dict | None:
        """Validate a WRT token.

        Returns the claims dict if valid, ``None`` if invalid, expired,
        or revoked.
        """
        jwt_mod = _import_jwt()

        if not token.startswith(_WRT_PREFIX):
            return None

        raw_jwt = token[len(_WRT_PREFIX) :]

        try:
            claims = jwt_mod.decode(
                raw_jwt,
                self._secret,
                algorithms=[_DEFAULT_ALGORITHM],
                options={"verify_aud": False},
            )
        except jwt_mod.ExpiredSignatureError:
            logger.debug("WRT token expired")
            return None
        except jwt_mod.InvalidTokenError as exc:
            logger.debug("WRT token invalid: %s", exc)
            return None

        jti = claims.get("jti")
        if jti and self.is_revoked(jti):
            logger.debug("WRT token revoked (jti=%s)", jti)
            return None

        return claims

    def revoke_token(self, token: str) -> None:
        """Revoke a WRT token by adding its JTI to the revocation store.

        The token is decoded (even if expired) to extract the JTI.
        """
        jwt_mod = _import_jwt()

        if not token.startswith(_WRT_PREFIX):
            return

        raw_jwt = token[len(_WRT_PREFIX) :]
        try:
            claims = jwt_mod.decode(
                raw_jwt,
                self._secret,
                algorithms=[_DEFAULT_ALGORITHM],
                options={
                    "verify_aud": False,
                    "verify_exp": False,
                },
            )
        except jwt_mod.InvalidTokenError:
            return

        jti = claims.get("jti")
        if jti:
            exp = claims.get("exp")
            expires_at = (
                datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
            )
            self._revocation_store.revoke_sync(jti, expires_at)

    def is_revoked(self, jti: str) -> bool:
        """Check if a token JTI has been revoked."""
        return self._revocation_store.is_revoked_sync(jti)


# ---------------------------------------------------------------------------
# Revocation Stores
# ---------------------------------------------------------------------------


class WRTRevocationStore(ABC):
    """Abstract store for persistent token revocation."""

    @abstractmethod
    async def revoke(self, jti: str, expires_at: datetime | None = None) -> None:
        """Add a JTI to the revocation list."""

    @abstractmethod
    async def is_revoked(self, jti: str) -> bool:
        """Check whether a JTI has been revoked."""

    def revoke_sync(self, jti: str, expires_at: datetime | None = None) -> None:
        """Synchronous revoke — override for non-async stores."""
        raise NotImplementedError(
            "Synchronous revoke not supported by this store"
        )

    def is_revoked_sync(self, jti: str) -> bool:
        """Synchronous check — override for non-async stores."""
        raise NotImplementedError(
            "Synchronous is_revoked not supported by this store"
        )


class InMemoryRevocationStore(WRTRevocationStore):
    """In-memory revocation store for testing and development."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    async def revoke(self, jti: str, expires_at: datetime | None = None) -> None:
        self._revoked.add(jti)

    async def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked

    def revoke_sync(self, jti: str, expires_at: datetime | None = None) -> None:
        self._revoked.add(jti)

    def is_revoked_sync(self, jti: str) -> bool:
        return jti in self._revoked
