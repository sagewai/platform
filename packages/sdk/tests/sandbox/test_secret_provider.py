"""Tests for SecretProvider seam and EnvSecretProvider default."""
import pytest

from sagewai.sandbox.secret_provider import EnvSecretProvider, SecretProvider


def test_env_secret_provider_is_a_secret_provider():
    assert isinstance(EnvSecretProvider({}), SecretProvider)


@pytest.mark.asyncio
async def test_env_provider_returns_project_scoped_secrets():
    store = {"acme": {"API_KEY": "abc", "DB_URL": "postgres://..."}}
    provider = EnvSecretProvider(store)
    env = await provider.env_for(
        project_id="acme",
        run_id="r1",
        agent_id=None,
        declared_scopes=[],
    )
    assert env == {"API_KEY": "abc", "DB_URL": "postgres://..."}


@pytest.mark.asyncio
async def test_env_provider_unknown_project_returns_empty():
    provider = EnvSecretProvider({})
    env = await provider.env_for(
        project_id="ghost",
        run_id="r1",
        agent_id=None,
        declared_scopes=[],
    )
    assert env == {}


@pytest.mark.asyncio
async def test_env_provider_never_leaks_host_env(monkeypatch):
    monkeypatch.setenv("HOST_SECRET", "must-not-leak")
    provider = EnvSecretProvider({"p1": {"KEY": "v1"}})
    env = await provider.env_for(
        project_id="p1", run_id="r1", agent_id=None, declared_scopes=[]
    )
    assert "HOST_SECRET" not in env
    assert env == {"KEY": "v1"}
