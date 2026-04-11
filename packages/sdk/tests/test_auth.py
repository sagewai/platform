"""Tests for auth module — JWT, API key, and middleware."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sagewai.auth.api_key import APIKeyAuth
from sagewai.auth.jwt import AuthenticationError, JWTAuth
from sagewai.auth.middleware import AuthConfig, require_auth

# ---------------------------------------------------------------------------
# JWT Auth
# ---------------------------------------------------------------------------


class TestJWTAuth:
    def test_create_and_verify(self):
        auth = JWTAuth(secret="test-secret")
        token = auth.create_token({"sub": "user-123", "role": "admin"})
        payload = auth.verify_token(token)
        assert payload["sub"] == "user-123"
        assert payload["role"] == "admin"
        assert "iat" in payload
        assert "exp" in payload

    def test_empty_secret_raises(self):
        with pytest.raises(ValueError, match="secret must not be empty"):
            JWTAuth(secret="")

    def test_expired_token(self):
        auth = JWTAuth(secret="test-secret")
        token = auth.create_token({"sub": "user"}, expiry_seconds=-1)
        with pytest.raises(AuthenticationError, match="expired"):
            auth.verify_token(token)

    def test_invalid_token(self):
        auth = JWTAuth(secret="test-secret")
        with pytest.raises(AuthenticationError, match="Invalid token"):
            auth.verify_token("not-a-valid-token")

    def test_wrong_secret(self):
        auth1 = JWTAuth(secret="secret-1")
        auth2 = JWTAuth(secret="secret-2")
        token = auth1.create_token({"sub": "user"})
        with pytest.raises(AuthenticationError):
            auth2.verify_token(token)

    def test_custom_expiry(self):
        auth = JWTAuth(secret="test-secret")
        token = auth.create_token({"sub": "user"}, expiry_seconds=7200)
        payload = auth.verify_token(token)
        assert payload["exp"] - payload["iat"] == 7200

    def test_issuer_claim(self):
        auth = JWTAuth(secret="test-secret", issuer="sagewai")
        token = auth.create_token({"sub": "user"})
        payload = auth.verify_token(token)
        assert payload["iss"] == "sagewai"

    def test_issuer_mismatch(self):
        auth1 = JWTAuth(secret="test-secret", issuer="sagewai")
        auth2 = JWTAuth(secret="test-secret", issuer="other")
        token = auth1.create_token({"sub": "user"})
        with pytest.raises(AuthenticationError):
            auth2.verify_token(token)

    def test_audience_claim(self):
        auth = JWTAuth(secret="test-secret", audience="nexus-api")
        token = auth.create_token({"sub": "user"})
        payload = auth.verify_token(token)
        assert payload["aud"] == "nexus-api"

    def test_refresh_token(self):
        auth = JWTAuth(secret="test-secret")
        token = auth.create_token({"sub": "user", "role": "admin"})
        new_token = auth.refresh_token(token)
        payload = auth.verify_token(new_token)
        assert payload["sub"] == "user"
        assert payload["role"] == "admin"

    def test_refresh_with_custom_expiry(self):
        auth = JWTAuth(secret="test-secret")
        token = auth.create_token({"sub": "user"})
        new_token = auth.refresh_token(token, expiry_seconds=86400)
        payload = auth.verify_token(new_token)
        assert payload["exp"] - payload["iat"] == 86400

    def test_requires_pyjwt(self):
        auth = JWTAuth(secret="test-secret")
        with patch.dict("sys.modules", {"jwt": None}):
            with pytest.raises(ImportError, match="pyjwt"):
                auth.create_token({"sub": "user"})


# ---------------------------------------------------------------------------
# API Key Auth
# ---------------------------------------------------------------------------


class TestAPIKeyAuth:
    def test_validate_valid_key(self):
        auth = APIKeyAuth(valid_keys=["sk-test-123"])
        assert auth.validate("sk-test-123") is True

    def test_validate_invalid_key(self):
        auth = APIKeyAuth(valid_keys=["sk-test-123"])
        with pytest.raises(AuthenticationError, match="Invalid API key"):
            auth.validate("sk-wrong-key")

    def test_validate_empty_key(self):
        auth = APIKeyAuth(valid_keys=["sk-test-123"])
        with pytest.raises(AuthenticationError, match="must not be empty"):
            auth.validate("")

    def test_is_valid(self):
        auth = APIKeyAuth(valid_keys=["sk-test-123"])
        assert auth.is_valid("sk-test-123") is True
        assert auth.is_valid("sk-wrong") is False
        assert auth.is_valid("") is False

    def test_add_key(self):
        auth = APIKeyAuth()
        assert auth.key_count == 0
        auth.add_key("new-key")
        assert auth.key_count == 1
        assert auth.is_valid("new-key") is True

    def test_revoke_key(self):
        auth = APIKeyAuth(valid_keys=["key-1", "key-2"])
        assert auth.key_count == 2
        assert auth.revoke_key("key-1") is True
        assert auth.key_count == 1
        assert auth.is_valid("key-1") is False
        assert auth.is_valid("key-2") is True

    def test_revoke_nonexistent(self):
        auth = APIKeyAuth(valid_keys=["key-1"])
        assert auth.revoke_key("nonexistent") is False

    def test_generate_key(self):
        key = APIKeyAuth.generate_key()
        assert key.startswith("sk-sage-")
        assert len(key) > 20

    def test_generate_keys_unique(self):
        keys = {APIKeyAuth.generate_key() for _ in range(10)}
        assert len(keys) == 10  # All unique

    def test_keys_stored_as_hashes(self):
        auth = APIKeyAuth(valid_keys=["my-secret-key"])
        # Internal hashes should not contain the raw key
        for h in auth._key_hashes:
            assert "my-secret-key" not in h

    def test_multiple_keys(self):
        auth = APIKeyAuth(valid_keys=["k1", "k2", "k3"])
        assert auth.key_count == 3
        assert auth.is_valid("k1")
        assert auth.is_valid("k2")
        assert auth.is_valid("k3")


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


class TestAuthConfig:
    def test_defaults(self):
        config = AuthConfig()
        assert config.jwt_secret is None
        assert config.api_keys == []
        assert config.header_name == "Authorization"
        assert config.api_key_header == "X-API-Key"

    def test_custom_config(self):
        config = AuthConfig(
            jwt_secret="secret",
            api_keys=["k1"],
            jwt_issuer="sagewai",
        )
        assert config.jwt_secret == "secret"
        assert config.api_keys == ["k1"]
        assert config.jwt_issuer == "sagewai"


# ---------------------------------------------------------------------------
# Middleware (require_auth)
# ---------------------------------------------------------------------------


class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_jwt_auth(self):
        config = AuthConfig(jwt_secret="test-secret")
        auth_dep = require_auth(config)

        jwt = JWTAuth(secret="test-secret")
        token = jwt.create_token({"sub": "user-123"})

        result = await auth_dep(authorization=f"Bearer {token}")
        assert result["sub"] == "user-123"

    @pytest.mark.asyncio
    async def test_api_key_auth(self):
        config = AuthConfig(api_keys=["sk-test-key"])
        auth_dep = require_auth(config)

        result = await auth_dep(api_key="sk-test-key")
        assert result["auth_type"] == "api_key"
        assert result["key_valid"] is True

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        config = AuthConfig(jwt_secret="test-secret")
        auth_dep = require_auth(config)

        with pytest.raises(AuthenticationError, match="Authentication required"):
            await auth_dep()

    @pytest.mark.asyncio
    async def test_invalid_jwt_falls_through(self):
        config = AuthConfig(jwt_secret="test-secret", api_keys=["sk-valid"])
        auth_dep = require_auth(config)

        # Invalid JWT but valid API key → should succeed via API key
        result = await auth_dep(authorization="Bearer invalid-token", api_key="sk-valid")
        assert result["auth_type"] == "api_key"

    @pytest.mark.asyncio
    async def test_jwt_without_bearer_prefix(self):
        config = AuthConfig(jwt_secret="test-secret")
        auth_dep = require_auth(config)

        jwt = JWTAuth(secret="test-secret")
        token = jwt.create_token({"sub": "user"})

        # Token without "Bearer " prefix should still work
        result = await auth_dep(authorization=token)
        assert result["sub"] == "user"

    @pytest.mark.asyncio
    async def test_neither_auth_method_configured(self):
        config = AuthConfig()  # No JWT secret, no API keys
        auth_dep = require_auth(config)

        with pytest.raises(AuthenticationError, match="Authentication required"):
            await auth_dep(authorization="Bearer something")
