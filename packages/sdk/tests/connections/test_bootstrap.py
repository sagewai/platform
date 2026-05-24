# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""ConnectionsContext / bootstrap tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import (
    ConnectionsContext,
    build_connections_context,
)
from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.protocols import PROTOCOLS
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.store import ConnectionStore


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    yield


@pytest.fixture
def _store_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    yield tmp_path


def test_build_connections_context_returns_dataclass(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    assert isinstance(ctx, ConnectionsContext)
    assert isinstance(ctx.store, ConnectionStore)
    assert isinstance(ctx.router, CredentialsBackendRouter)


def test_store_has_all_5_protocols_allowed(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    # The store accepts any protocol the registry knows about.
    expected_ids = {p.id for p in PROTOCOLS}
    actual_ids = set(ctx.store._allowed_protocols)
    assert actual_ids == expected_ids


def test_store_uses_default_key_for_from_registry(tmp_path: Path, _store_env):
    """oauth2 + inference get per-provider/per-provider_key defaults."""
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    # Two spotify oauth2 clients should both NOT be default of each other
    # (matching the integration test from PR2).
    a = ctx.store.create(
        protocol="oauth2", project_id="default", display_name="A",
        tags=[], protocol_data={
            "provider": "spotify", "client_id": "c", "client_secret": "s",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": ["s1"], "granted_scopes": [], "tokens": None,
        },
    )
    b = ctx.store.create(
        protocol="oauth2", project_id="default", display_name="B",
        tags=[], protocol_data={
            "provider": "spotify", "client_id": "c", "client_secret": "s",
            "redirect_uri": "http://localhost/cb",
            "requested_scopes": ["s1"], "granted_scopes": [], "tokens": None,
        },
    )
    assert a.is_default is True
    assert b.is_default is False  # same provider — second is NOT default


def test_router_uses_admin_state_default_backend(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    sf.set_default_credentials_backend("env")
    ctx = build_connections_context(sf)
    backend, _ = ctx.router.get_backend_for(None)
    assert backend.id == "env"


def test_router_falls_back_to_local_when_state_file_silent(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    # Don't set default_credentials_backend; it returns "local" per PR3 default.
    ctx = build_connections_context(sf)
    backend, _ = ctx.router.get_backend_for(None)
    assert backend.id == "local"


def test_make_plugin_context_returns_plugincontext(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    pc = ctx.make_plugin_context(project_id="default", request=None)
    assert isinstance(pc, PluginContext)
    assert pc.store is ctx.store
    assert pc.creds is ctx.router
    assert pc.project_id == "default"
    assert pc.request is None


def test_make_plugin_context_threads_through_project_id(tmp_path: Path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    pc_a = ctx.make_plugin_context(project_id="a", request=None)
    pc_b = ctx.make_plugin_context(project_id="b", request=None)
    assert pc_a.project_id == "a"
    assert pc_b.project_id == "b"
    # store and router are shared across requests (cheap to construct)
    assert pc_a.store is pc_b.store
    assert pc_a.creds is pc_b.creds
