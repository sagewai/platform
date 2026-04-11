"""Tests for sagewai.fleet.auth — WRTTokenManager and revocation stores."""

from __future__ import annotations

import time

import pytest

from sagewai.fleet.auth import (
    InMemoryRevocationStore,
    WRTTokenManager,
)


@pytest.fixture
def manager() -> WRTTokenManager:
    return WRTTokenManager(secret="test-secret-key-for-wrt")


# ---------------------------------------------------------------------------
# Token Issuance
# ---------------------------------------------------------------------------


def test_issue_token_returns_prefixed_string(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    assert token.startswith("wrt-1.")
    # Should have two dots in the JWT part after prefix
    jwt_part = token[len("wrt-1."):]
    assert jwt_part.count(".") == 2


def test_issue_token_default_scopes(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    claims = manager.validate_token(token)
    assert claims is not None
    assert claims["scopes"] == ["claim", "report", "heartbeat"]


def test_issue_token_custom_scopes(manager: WRTTokenManager):
    token = manager.issue_token(
        worker_id="w-1", org_id="org-1", scopes=["claim"]
    )
    claims = manager.validate_token(token)
    assert claims is not None
    assert claims["scopes"] == ["claim"]


def test_issue_token_custom_pool(manager: WRTTokenManager):
    token = manager.issue_token(
        worker_id="w-1", org_id="org-1", pool="gpu-cluster"
    )
    claims = manager.validate_token(token)
    assert claims is not None
    assert claims["pool"] == "gpu-cluster"


# ---------------------------------------------------------------------------
# Token Validation
# ---------------------------------------------------------------------------


def test_validate_valid_token(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    claims = manager.validate_token(token)
    assert claims is not None
    assert claims["sub"] == "w-1"
    assert claims["org"] == "org-1"
    assert claims["pool"] == "default"
    assert "jti" in claims
    assert "iat" in claims
    assert "exp" in claims


def test_validate_invalid_prefix(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    # Remove the prefix
    bad_token = token.replace("wrt-1.", "bad-prefix.")
    assert manager.validate_token(bad_token) is None


def test_validate_garbage_token(manager: WRTTokenManager):
    assert manager.validate_token("wrt-1.not.a.valid.jwt") is None


def test_validate_wrong_secret():
    mgr1 = WRTTokenManager(secret="secret-1")
    mgr2 = WRTTokenManager(secret="secret-2")
    token = mgr1.issue_token(worker_id="w-1", org_id="org-1")
    assert mgr2.validate_token(token) is None


def test_validate_expired_token():
    mgr = WRTTokenManager(secret="test-secret", default_expiry_seconds=1)
    token = mgr.issue_token(
        worker_id="w-1", org_id="org-1", expiry_seconds=0
    )
    # Token with 0 expiry should be expired immediately (or within 1s)
    # We need to wait a moment for the exp to be in the past
    time.sleep(1.5)
    assert mgr.validate_token(token) is None


def test_validate_no_prefix(manager: WRTTokenManager):
    assert manager.validate_token("") is None
    assert manager.validate_token("random-string") is None


# ---------------------------------------------------------------------------
# Token Revocation
# ---------------------------------------------------------------------------


def test_revoke_and_reject(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    # Valid before revocation
    assert manager.validate_token(token) is not None

    manager.revoke_token(token)

    # Invalid after revocation
    assert manager.validate_token(token) is None


def test_revoke_invalid_token_is_noop(manager: WRTTokenManager):
    # Should not raise
    manager.revoke_token("wrt-1.garbage")
    manager.revoke_token("no-prefix-at-all")


def test_is_revoked(manager: WRTTokenManager):
    token = manager.issue_token(worker_id="w-1", org_id="org-1")
    claims = manager.validate_token(token)
    assert claims is not None
    jti = claims["jti"]

    assert not manager.is_revoked(jti)
    manager.revoke_token(token)
    assert manager.is_revoked(jti)


# ---------------------------------------------------------------------------
# Revocation Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_revocation_store():
    store = InMemoryRevocationStore()
    assert not await store.is_revoked("jti-1")
    await store.revoke("jti-1")
    assert await store.is_revoked("jti-1")


@pytest.mark.asyncio
async def test_in_memory_revocation_store_sync():
    store = InMemoryRevocationStore()
    assert not store.is_revoked_sync("jti-1")
    store.revoke_sync("jti-1")
    assert store.is_revoked_sync("jti-1")


# ---------------------------------------------------------------------------
# Constructor Validation
# ---------------------------------------------------------------------------


def test_empty_secret_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        WRTTokenManager(secret="")


# ---------------------------------------------------------------------------
# Custom Revocation Store
# ---------------------------------------------------------------------------


def test_custom_revocation_store():
    store = InMemoryRevocationStore()
    mgr = WRTTokenManager(secret="test", revocation_store=store)

    token = mgr.issue_token(worker_id="w-1", org_id="org-1")
    assert mgr.validate_token(token) is not None

    mgr.revoke_token(token)
    assert mgr.validate_token(token) is None


# ---------------------------------------------------------------------------
# Multiple Tokens
# ---------------------------------------------------------------------------


def test_multiple_tokens_independent(manager: WRTTokenManager):
    t1 = manager.issue_token(worker_id="w-1", org_id="org-1")
    t2 = manager.issue_token(worker_id="w-2", org_id="org-1")

    # Both valid
    assert manager.validate_token(t1) is not None
    assert manager.validate_token(t2) is not None

    # Revoke only t1
    manager.revoke_token(t1)
    assert manager.validate_token(t1) is None
    assert manager.validate_token(t2) is not None
