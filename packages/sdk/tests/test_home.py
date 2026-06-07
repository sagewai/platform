# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the SAGEWAI_HOME resolver and directory helpers."""
from pathlib import Path

import pytest

from sagewai import home


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    yield


def test_sagewai_home_uses_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("SAGEWAI_HOME", str(target))
    assert home.sagewai_home() == target.resolve()


def test_sagewai_home_defaults_to_dot_sagewai(monkeypatch):
    monkeypatch.delenv("SAGEWAI_HOME", raising=False)
    assert home.sagewai_home() == (Path.home() / ".sagewai").resolve()


def test_dir_helpers_create_subdirs():
    assert home.config_dir().is_dir()
    assert home.db_dir().is_dir()
    assert home.data_dir().is_dir()
    assert home.secrets_dir().is_dir()
    assert home.config_dir().name == "config"
    assert home.db_dir().name == "db"
    assert home.data_dir().name == "data"
    assert home.secrets_dir().name == "secrets"


def test_secrets_dir_is_private():
    mode = home.secrets_dir().stat().st_mode & 0o777
    assert mode == 0o700


def test_sagewai_home_expands_tilde(monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", "~/sagewai_tilde_test")
    assert home.sagewai_home() == (Path.home() / "sagewai_tilde_test").resolve()


def test_migrate_home_relocates_flat_files(tmp_path, monkeypatch):
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    base.mkdir(parents=True)
    (base / "admin-state.json").write_text('{"v":1}')
    (base / "connections.json").write_text('{"connections":[]}')
    (base / "master.key").write_text("k")
    (base / "profiles.json").write_text('{"profiles":[]}')

    moved = home.migrate_home()

    assert (home.config_dir() / "admin-state.json").read_text() == '{"v":1}'
    assert (home.config_dir() / "connections.json").exists()
    assert (home.secrets_dir() / "master.key").read_text() == "k"
    assert (home.secrets_dir() / "profiles.json").exists()
    assert not (base / "admin-state.json").exists()
    assert set(moved) == {"admin-state.json", "connections.json", "master.key", "profiles.json"}


def test_migrate_home_is_idempotent(tmp_path, monkeypatch):
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    base.mkdir(parents=True)
    (base / "admin-state.json").write_text("{}")
    home.migrate_home()
    assert home.migrate_home() == []  # second run is a no-op


def test_migrate_home_does_not_clobber_existing_target(tmp_path, monkeypatch):
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    (base / "config").mkdir(parents=True)
    (base / "config" / "admin-state.json").write_text("NEW")
    (base / "admin-state.json").write_text("OLD")
    moved = home.migrate_home()
    assert moved == []
    assert (base / "config" / "admin-state.json").read_text() == "NEW"  # target wins, legacy left in place
    assert (base / "admin-state.json").exists()


# ---------------------------------------------------------------------------
# FIX 3 — per-file override env vars suppress migration
# ---------------------------------------------------------------------------


def test_migrate_home_skips_admin_state_when_override_file_set(tmp_path, monkeypatch):
    """SAGEWAI_ADMIN_STATE_FILE pins the path — migrate must not move the file."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    base.mkdir(parents=True)
    legacy = base / "admin-state.json"
    legacy.write_text('{"setup_complete": true}')
    override_path = str(legacy)  # operator pinned at the legacy location
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", override_path)

    moved = home.migrate_home()

    assert "admin-state.json" not in moved
    assert legacy.exists(), "legacy file must NOT have been moved"
    # config/ may or may not have been created, but must NOT contain the file
    config = base / "config"
    assert not (config / "admin-state.json").exists()


def test_migrate_home_skips_admin_state_when_alias_set(tmp_path, monkeypatch):
    """SAGEWAI_ADMIN_STATE (CLI alias) also suppresses migration."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    base.mkdir(parents=True)
    legacy = base / "admin-state.json"
    legacy.write_text('{"setup_complete": true}')
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE", str(legacy))

    moved = home.migrate_home()

    assert "admin-state.json" not in moved
    assert legacy.exists()


def test_migrate_home_skips_connections_when_override_set(tmp_path, monkeypatch):
    """SAGEWAI_CONNECTIONS_FILE pins the path — migrate must not move the file."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    base.mkdir(parents=True)
    legacy = base / "connections.json"
    legacy.write_text('{"connections": []}')
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(legacy))

    moved = home.migrate_home()

    assert "connections.json" not in moved
    assert legacy.exists()
    assert not (base / "config" / "connections.json").exists()


def test_migrate_home_still_moves_admin_state_without_override(tmp_path, monkeypatch):
    """Without override env vars, admin-state.json IS migrated normally."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    monkeypatch.delenv("SAGEWAI_ADMIN_STATE_FILE", raising=False)
    monkeypatch.delenv("SAGEWAI_ADMIN_STATE", raising=False)
    base.mkdir(parents=True)
    (base / "admin-state.json").write_text('{"setup_complete": true}')

    moved = home.migrate_home()

    assert "admin-state.json" in moved
    assert not (base / "admin-state.json").exists()
    assert (base / "config" / "admin-state.json").exists()


def test_migrate_home_still_moves_connections_without_override(tmp_path, monkeypatch):
    """Without SAGEWAI_CONNECTIONS_FILE, connections.json IS migrated normally."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    monkeypatch.delenv("SAGEWAI_CONNECTIONS_FILE", raising=False)
    base.mkdir(parents=True)
    (base / "connections.json").write_text('{"connections": []}')

    moved = home.migrate_home()

    assert "connections.json" in moved
    assert not (base / "connections.json").exists()
    assert (base / "config" / "connections.json").exists()


def test_migrate_home_always_moves_master_key_no_path_override(tmp_path, monkeypatch):
    """master.key has no file-path override — it is always migrated (SAGEWAI_MASTER_KEY
    is the key VALUE, not a path, so it does not suppress migration)."""
    base = tmp_path / "home"
    monkeypatch.setenv("SAGEWAI_HOME", str(base))
    # Even if SAGEWAI_MASTER_KEY is set, the legacy file is migrated
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "x" * 44)
    base.mkdir(parents=True)
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    (base / "master.key").write_text(key)

    moved = home.migrate_home()

    assert "master.key" in moved
    assert not (base / "master.key").exists()
    assert (base / "secrets" / "master.key").exists()
