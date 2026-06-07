# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Integration tests: per-module defaults resolve under the SAGEWAI_HOME layout."""
import pytest


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    for v in ("SAGEWAI_ADMIN_STATE_FILE", "SAGEWAI_CONNECTIONS_FILE", "SAGEWAI_SOPS_ROOT"):
        monkeypatch.delenv(v, raising=False)
    yield


def test_admin_state_default_under_config():
    from sagewai import home
    from sagewai.admin.state_file import default_admin_state_path
    assert default_admin_state_path() == home.config_dir() / "admin-state.json"


def test_admin_state_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "x.json"))
    from sagewai.admin.state_file import default_admin_state_path
    assert default_admin_state_path() == tmp_path / "x.json"


def test_connections_default_under_config():
    from sagewai import home
    from sagewai.connections.store import _default_store_path
    assert _default_store_path() == home.config_dir() / "connections.json"


def test_connections_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "c.json"))
    from sagewai.connections.store import _default_store_path
    assert _default_store_path() == tmp_path / "c.json"


def test_master_key_default_under_secrets():
    from sagewai import home
    from sagewai.sealed.master_key import default_key_path
    assert default_key_path() == home.secrets_dir() / "master.key"


def test_profiles_default_under_secrets():
    from sagewai import home
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    b = BuiltinAdminStoreBackend()
    assert b._path == home.secrets_dir() / "profiles.json"


def test_sops_root_default_under_secrets():
    from sagewai import home
    from sagewai.connections.credentials.sops import _sops_root
    assert _sops_root() == (home.secrets_dir() / "sops").resolve()


def test_sops_root_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_SOPS_ROOT", str(tmp_path / "s"))
    from sagewai.connections.credentials.sops import _sops_root
    assert _sops_root() == (tmp_path / "s")


def test_blueprint_cache_default_under_data(monkeypatch):
    monkeypatch.delenv("SAGEWAI_CACHE_DIR", raising=False)
    from sagewai import home
    from sagewai.admin import autopilot_routes
    assert autopilot_routes._blueprint_cache_dir() == home.data_dir() / "blueprint_cache"


def test_blueprint_cache_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_CACHE_DIR", str(tmp_path / "bc"))
    from sagewai.admin import autopilot_routes
    assert autopilot_routes._blueprint_cache_dir() == tmp_path / "bc"


def test_cli_admin_state_fallback_matches_helper(monkeypatch):
    monkeypatch.delenv("SAGEWAI_ADMIN_STATE_FILE", raising=False)
    monkeypatch.delenv("SAGEWAI_ADMIN_STATE", raising=False)
    from sagewai.admin.state_file import default_admin_state_path
    from sagewai.cli import sandbox as cli_sandbox
    assert cli_sandbox.resolve_admin_state_path() == default_admin_state_path()


def test_cli_admin_state_alias_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE", str(tmp_path / "a.json"))
    from sagewai.cli import sandbox as cli_sandbox
    assert cli_sandbox.resolve_admin_state_path() == tmp_path / "a.json"


def test_blueprint_cache_empty_env_uses_default(monkeypatch):
    monkeypatch.setenv("SAGEWAI_CACHE_DIR", "")
    from sagewai import home
    from sagewai.admin import autopilot_routes
    assert autopilot_routes._blueprint_cache_dir() == home.data_dir() / "blueprint_cache"


def test_cli_admin_state_file_env_honoured_through_helper(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_ADMIN_STATE", raising=False)
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "b.json"))
    from sagewai.cli import sandbox as cli_sandbox
    assert cli_sandbox.resolve_admin_state_path() == tmp_path / "b.json"


def test_create_admin_serve_app_runs_migration(tmp_path, monkeypatch):
    base = tmp_path / "home"
    base.mkdir(parents=True)
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    (base / "admin-state.json").write_text('{"setup_complete": false}')
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    create_admin_serve_app(AdminStateFile())
    from sagewai import home
    assert (home.config_dir() / "admin-state.json").exists()
    assert not (base / "admin-state.json").exists()


# ---------------------------------------------------------------------------
# FIX 4b — resolve_master_key() legacy fallback
# ---------------------------------------------------------------------------


def test_resolve_master_key_falls_back_to_legacy_path(tmp_path, monkeypatch):
    """If secrets/master.key is absent but the legacy root master.key exists,
    resolve_master_key() should return the key (source 'file') not raise."""
    from cryptography.fernet import Fernet
    base = tmp_path / "home"
    base.mkdir(parents=True)
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)

    fake_key = Fernet.generate_key()
    legacy_key = base / "master.key"
    legacy_key.write_bytes(fake_key)
    legacy_key.chmod(0o600)

    # Patch keyring out so only the file branch runs.
    import sagewai.sealed.master_key as mk_mod
    monkeypatch.setattr(mk_mod, "keyring", None)

    from sagewai.sealed.master_key import resolve_master_key
    key, source = resolve_master_key()
    assert key == fake_key
    assert source == "file"


def test_resolve_master_key_prefers_new_path_over_legacy(tmp_path, monkeypatch):
    """When both paths exist the canonical secrets/master.key wins."""
    from cryptography.fernet import Fernet
    base = tmp_path / "home"
    base.mkdir(parents=True)
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)

    canonical_key = Fernet.generate_key()
    legacy_key = Fernet.generate_key()

    # Create canonical location
    secrets = base / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "master.key").write_bytes(canonical_key)
    (secrets / "master.key").chmod(0o600)

    # Create legacy location with different key
    (base / "master.key").write_bytes(legacy_key)
    (base / "master.key").chmod(0o600)

    import sagewai.sealed.master_key as mk_mod
    monkeypatch.setattr(mk_mod, "keyring", None)

    from sagewai.sealed.master_key import resolve_master_key
    key, source = resolve_master_key()
    assert key == canonical_key  # canonical wins


# ---------------------------------------------------------------------------
# FIX 4a — CLI group runs migrate_home()
# ---------------------------------------------------------------------------


def test_cli_group_runs_migrate_home(tmp_path, monkeypatch):
    """Invoking the CLI group (no subcommand → prints help) migrates legacy flat files.

    Note: Click's --help flag is processed before the callback body runs, so we
    invoke with no args instead — the cli() callback fires, runs migrate_home(),
    then prints help because ctx.invoked_subcommand is None.
    """
    from click.testing import CliRunner
    base = tmp_path / "home"
    base.mkdir(parents=True)
    (base / "admin-state.json").write_text('{"setup_complete": true}')

    from sagewai.cli import cli
    # Pass SAGEWAI_HOME override via CliRunner env so the subprocess-like
    # invocation sees the tmp path.  Per-file overrides are cleared so that
    # migration is not suppressed.
    env_override = {
        "SAGEWAI_HOME": str(base),
        "SAGEWAI_ADMIN_STATE_FILE": "",
        "SAGEWAI_ADMIN_STATE": "",
        "SAGEWAI_CONNECTIONS_FILE": "",
    }
    runner = CliRunner()
    result = runner.invoke(cli, [], env=env_override)
    # No subcommand → prints help, exit 0
    assert result.exit_code == 0

    # Migration must have relocated the file
    assert not (base / "admin-state.json").exists()
    assert (base / "config" / "admin-state.json").exists(), \
        "admin-state.json should have been relocated by cli startup migration"


def test_cli_group_no_spurious_dirs_when_no_legacy_files(tmp_path, monkeypatch):
    """CLI invocation on a fresh install (no legacy files) must not create subdirs."""
    from click.testing import CliRunner
    base = tmp_path / "home"
    # Do NOT create base — simulate a truly fresh install (no ~/.sagewai yet).

    from sagewai.cli import cli
    env_override = {
        "SAGEWAI_HOME": str(base),
        "SAGEWAI_ADMIN_STATE_FILE": "",
        "SAGEWAI_ADMIN_STATE": "",
        "SAGEWAI_CONNECTIONS_FILE": "",
    }
    runner = CliRunner()
    result = runner.invoke(cli, [], env=env_override)
    assert result.exit_code == 0
    # migrate_home() itself does NOT create subdirs when there's nothing to move
    # (subdir helpers are only called when a legacy file exists)
    assert not (base / "config").exists()
    assert not (base / "secrets").exists()
