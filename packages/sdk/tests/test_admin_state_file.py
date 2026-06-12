# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the admin state file store."""

import json

import pytest
from cryptography.fernet import Fernet

from sagewai.admin.state_file import AdminStateFile


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    yield


@pytest.fixture
def sf(tmp_path):
    """Create a state file in a temp directory."""
    return AdminStateFile(tmp_path / "state.json")


class TestSetup:
    def test_fresh_state_requires_setup(self, sf):
        assert not sf.is_setup_complete()

    def test_complete_setup_creates_org_and_project(self, sf):
        result = sf.complete_setup(
            org_name="Test Corp",
            org_slug="test-corp",
            admin_email="admin@test.com",
            admin_password="pass1234",
            app_name="My App",
        )
        assert result["ok"] is True
        assert result["org_slug"] == "test-corp"
        assert result["app_slug"] == "my-app"
        assert sf.is_setup_complete()

    def test_setup_creates_default_project(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="App",
        )
        projects = sf.list_projects()
        assert len(projects) == 1
        assert projects[0]["slug"] == "app"
        assert projects[0]["name"] == "App"
        assert projects[0]["status"] == "active"

    def test_double_setup_fails(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x",
        )
        result = sf.complete_setup(
            org_name="Corp2", admin_email="b@c.com",
            admin_password="y",
        )
        assert result["ok"] is False


class TestOrg:
    def test_get_org_after_setup(self, sf):
        sf.complete_setup(
            org_name="Acme", org_slug="acme",
            contact_email="hello@acme.com", timezone="Europe/Berlin",
            admin_email="admin@acme.com", admin_password="pass",
        )
        org = sf.get_org()
        assert org["org_name"] == "Acme"
        assert org["org_slug"] == "acme"
        assert org["contact_email"] == "hello@acme.com"
        assert org["timezone"] == "Europe/Berlin"
        assert org["admin_email"] == "admin@acme.com"

    def test_update_org(self, sf):
        sf.complete_setup(
            org_name="Old", admin_email="a@b.com", admin_password="x",
        )
        updated = sf.update_org({"org_name": "New", "industry": "Tech"})
        assert updated["org_name"] == "New"
        assert updated["industry"] == "Tech"
        # Read-only fields not changed
        assert updated["org_slug"] != ""

    def test_update_org_ignores_readonly(self, sf):
        sf.complete_setup(
            org_name="Corp", org_slug="corp",
            admin_email="a@b.com", admin_password="x",
        )
        sf.update_org({"org_slug": "hacked", "admin_email": "hacked@x.com"})
        org = sf.get_org()
        assert org["org_slug"] == "corp"  # not changed


class TestProjects:
    def test_create_and_list(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="Default",
        )
        sf.create_project(name="Second Project")
        projects = sf.list_projects()
        assert len(projects) == 2
        assert projects[1]["name"] == "Second Project"

    def test_update_project(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="App",
        )
        result = sf.update_project("app", {"default_model": "gpt-4o"})
        assert result is not None
        assert result["default_model"] == "gpt-4o"

    def test_delete_default_project_fails(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="App",
        )
        with pytest.raises(ValueError, match="default"):
            sf.delete_project("app")

    def test_delete_non_default_project(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="App",
        )
        sf.create_project(name="Temp")
        assert sf.delete_project("temp") is True
        assert len(sf.list_projects()) == 1

    def test_duplicate_slug_fails(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", app_name="App",
        )
        with pytest.raises(ValueError, match="already exists"):
            sf.create_project(name="App")


class TestProviders:
    def test_upsert_and_list(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com", admin_password="x",
        )
        sf.upsert_provider({
            "provider_name": "openai",
            "display_name": "OpenAI",
            "config": {"api_key": "sk-test"},
        })
        providers = sf.list_providers()
        assert len(providers) == 1
        assert providers[0]["provider_name"] == "openai"
        # ID is now project-scoped: prov-{project_id or 'global'}-{provider_name}
        assert providers[0]["id"] == "prov-global-openai"
        # Secrets are redacted in list_providers — api_key stripped, api_key_set present
        assert providers[0]["config"].get("api_key_set") is True
        assert "api_key" not in providers[0]["config"]
        # Plaintext is still accessible via the decrypted accessor
        rec = sf.get_provider_decrypted("openai")
        assert rec["config"]["api_key"] == "sk-test"

    def test_upsert_updates_existing(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com", admin_password="x",
        )
        sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "old"}})
        sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "new"}})
        providers = sf.list_providers()
        assert len(providers) == 1
        # Secrets are redacted in list_providers
        assert providers[0]["config"].get("api_key_set") is True
        assert "api_key" not in providers[0]["config"]
        # Verify the update took effect via the decrypted accessor
        rec = sf.get_provider_decrypted("openai")
        assert rec["config"]["api_key"] == "new"

    def test_delete_provider(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com", admin_password="x",
        )
        sf.upsert_provider({"provider_name": "openai", "config": {}})
        # Derive the id from the returned/listed record rather than hardcoding old form
        providers = sf.list_providers()
        provider_id = providers[0]["id"]
        assert provider_id == "prov-global-openai"
        assert sf.delete_provider(provider_id) is True
        assert len(sf.list_providers()) == 0


class TestAuth:
    def test_login_success(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="secret123",
        )
        result = sf.validate_login("a@b.com", "secret123")
        assert result is not None
        assert "access_token" in result
        assert result["user"]["email"] == "a@b.com"
        assert result["user"]["role"] == "admin"

    def test_login_wrong_password(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="secret123",
        )
        assert sf.validate_login("a@b.com", "wrong") is None

    def test_login_wrong_email(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="secret123",
        )
        assert sf.validate_login("wrong@b.com", "secret123") is None

    def test_token_validation(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x",
        )
        result = sf.validate_login("a@b.com", "x")
        token = result["access_token"]
        assert sf.validate_token(token) is True
        assert sf.validate_token("garbage") is False

    def test_get_user_by_token(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x", admin_name="Alice",
        )
        result = sf.validate_login("a@b.com", "x")
        user = sf.get_user_by_token(result["access_token"])
        assert user is not None
        assert user["email"] == "a@b.com"
        assert user["display_name"] == "Alice"

    def test_refresh_token(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x",
        )
        result = sf.validate_login("a@b.com", "x")
        old_token = result["access_token"]
        refreshed = sf.refresh_token(old_token)
        assert refreshed is not None
        new_token = refreshed["access_token"]
        assert new_token != old_token
        assert sf.validate_token(new_token) is True

    def test_multiple_tokens_supported(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com",
            admin_password="x",
        )
        t1 = sf.validate_login("a@b.com", "x")["access_token"]
        t2 = sf.validate_login("a@b.com", "x")["access_token"]
        assert sf.validate_token(t1) is True
        assert sf.validate_token(t2) is True


class TestMigration:
    def test_old_format_auto_migrates(self, sf, tmp_path):
        """Old state with active_token (singular) and no projects."""
        old_state = {
            "setup_complete": True,
            "setup_at": "2026-04-12T00:00:00Z",
            "org_name": "OldCorp",
            "org_slug": "oldcorp",
            "app_name": "OldApp",
            "app_slug": "oldapp",
            "admin": {
                "id": "abc",
                "email": "old@test.com",
                "name": "Old Admin",
                "password_hash": "x",
                "password_salt": "y",
                "role": "admin",
            },
            "active_token": "old-token-123",
        }
        (tmp_path / "state.json").write_text(json.dumps(old_state))

        # Projects should auto-create from app_slug
        projects = sf.list_projects()
        assert len(projects) == 1
        assert projects[0]["slug"] == "oldapp"
        assert projects[0]["name"] == "OldApp"

    def test_reset(self, sf):
        sf.complete_setup(
            org_name="Corp", admin_email="a@b.com", admin_password="x",
        )
        sf.reset()
        assert not sf.is_setup_complete()


def test_admin_state_get_agent_known(tmp_path, monkeypatch):
    """get_agent returns the agent dict when present."""
    import json

    from sagewai.admin.state_file import AdminStateFile

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({
        "agents": [
            {"name": "writer", "model": "gpt-4o", "sandbox_requirements_override": None},
            {"name": "researcher", "model": "claude-3"},
        ]
    }))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    state = AdminStateFile()
    agent = state.get_agent("writer")
    assert agent is not None
    assert agent["model"] == "gpt-4o"


def test_admin_state_get_agent_missing(tmp_path, monkeypatch):
    """get_agent returns None for unknown name."""
    import json

    from sagewai.admin.state_file import AdminStateFile

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"agents": []}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    state = AdminStateFile()
    assert state.get_agent("ghost") is None


def test_admin_state_get_agent_no_agents_key(tmp_path, monkeypatch):
    """get_agent returns None if 'agents' key missing entirely."""
    import json

    from sagewai.admin.state_file import AdminStateFile

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"projects": []}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    state = AdminStateFile()
    assert state.get_agent("writer") is None


def test_admin_state_set_agent_sandbox_override_round_trip(tmp_path, monkeypatch):
    """Writing an override field then re-reading round-trips through the JSON."""
    import json

    from sagewai.admin.state_file import AdminStateFile

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"agents": [{"name": "writer", "model": "gpt-4o"}]}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    state = AdminStateFile()
    data = state._read()
    agent = next(a for a in data["agents"] if a["name"] == "writer")
    agent["sandbox_requirements_override"] = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
        "network_policy": "full",
        "required_secret_scopes": [],
    }
    state._write(data)

    fresh = AdminStateFile()
    reloaded = fresh.get_agent("writer")
    assert reloaded["sandbox_requirements_override"]["sandbox_mode"] == "per_run"
    assert reloaded["model"] == "gpt-4o"   # other fields preserved


def test_cancel_agent_run_flips_status(sf):
    """cancel_agent_run marks the run cancelled and returns True (single-org)."""
    sf.save_agent_run({"run_id": "r1", "agent_name": "scout", "status": "running"})
    assert sf.cancel_agent_run("r1") is True
    assert sf.get_agent_run("r1")["status"] == "cancelled"


def test_cancel_agent_run_unknown_id_returns_false(sf):
    """An unknown run id is a no-op and returns False (route maps it to 404)."""
    assert sf.cancel_agent_run("nope") is False
