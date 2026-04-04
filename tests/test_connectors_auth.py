# packages/sagewai/tests/test_connectors_auth.py
import os
import pytest
from unittest.mock import patch
from sagewai.connectors.auth import CredentialResolver
from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec
from sagewai.connectors.stores import InMemoryCredentialStore


def _make_spec(fields: list[AuthField] | None = None) -> ConnectorSpec:
    return ConnectorSpec(
        name="test", display_name="Test", category="test",
        description="Test", auth_type=AuthType.API_KEY,
        auth_fields=fields or [
            AuthField(key="api_key", label="API Key", env_var="TEST_API_KEY"),
        ],
        mcp_command=["echo"],
    )


@pytest.mark.asyncio
async def test_resolve_from_env():
    resolver = CredentialResolver()
    spec = _make_spec()
    with patch.dict(os.environ, {"TEST_API_KEY": "env-value"}):
        creds = await resolver.resolve(spec)
    assert creds == {"api_key": "env-value"}


@pytest.mark.asyncio
async def test_resolve_from_store():
    store = InMemoryCredentialStore()
    await store.put("test", {"api_key": "db-value"})
    resolver = CredentialResolver(store=store)
    spec = _make_spec()
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_API_KEY", None)
        creds = await resolver.resolve(spec)
    assert creds == {"api_key": "db-value"}


@pytest.mark.asyncio
async def test_env_overrides_store():
    store = InMemoryCredentialStore()
    await store.put("test", {"api_key": "db-value"})
    resolver = CredentialResolver(store=store)
    spec = _make_spec()
    with patch.dict(os.environ, {"TEST_API_KEY": "env-value"}):
        creds = await resolver.resolve(spec)
    assert creds == {"api_key": "env-value"}


@pytest.mark.asyncio
async def test_resolve_empty_when_nothing_set():
    resolver = CredentialResolver()
    spec = _make_spec()
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_API_KEY", None)
        creds = await resolver.resolve(spec)
    assert creds == {}


@pytest.mark.asyncio
async def test_resolve_multiple_fields():
    spec = _make_spec(fields=[
        AuthField(key="client_id", label="Client ID", env_var="TEST_CLIENT_ID", secret=False),
        AuthField(key="client_secret", label="Client Secret", env_var="TEST_CLIENT_SECRET"),
    ])
    resolver = CredentialResolver()
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "id-123", "TEST_CLIENT_SECRET": "sec-456"}):
        creds = await resolver.resolve(spec)
    assert creds == {"client_id": "id-123", "client_secret": "sec-456"}


# --- OAuth2Client tests ---

from sagewai.connectors.auth import OAuth2Client
from sagewai.connectors.stores import InMemoryOAuthTokenStore


def _make_oauth_spec() -> ConnectorSpec:
    return ConnectorSpec(
        name="x", display_name="X", category="communication",
        description="X/Twitter", auth_type=AuthType.OAUTH2,
        auth_fields=[
            AuthField(key="client_id", label="Client ID", env_var="X_CLIENT_ID", secret=False),
            AuthField(key="client_secret", label="Client Secret", env_var="X_CLIENT_SECRET"),
        ],
        mcp_command=["echo"],
        oauth_authorize_url="https://twitter.com/i/oauth2/authorize",
        oauth_token_url="https://api.twitter.com/2/oauth2/token",
        oauth_scopes=["tweet.read", "users.read"],
    )


@pytest.mark.asyncio
async def test_oauth2_get_authorization_url():
    client = OAuth2Client()
    spec = _make_oauth_spec()
    with patch.dict(os.environ, {"X_CLIENT_ID": "my-client-id"}):
        url, state = await client.get_authorization_url(spec, "http://localhost/callback")
    assert "twitter.com" in url
    assert "my-client-id" in url
    assert "state=" in url
    assert len(state) > 0


@pytest.mark.asyncio
async def test_oauth2_invalid_state_raises():
    client = OAuth2Client()
    spec = _make_oauth_spec()
    with pytest.raises(ValueError, match="Invalid or expired state"):
        await client.exchange_code(spec, "code-123", "bad-state")


@pytest.mark.asyncio
async def test_oauth2_no_authorize_url_raises():
    client = OAuth2Client()
    spec = ConnectorSpec(
        name="test", display_name="Test", category="test",
        description="Test", auth_type=AuthType.OAUTH2,
        auth_fields=[], mcp_command=["echo"],
    )
    with pytest.raises(ValueError, match="no oauth_authorize_url"):
        await client.get_authorization_url(spec, "http://localhost/callback")
