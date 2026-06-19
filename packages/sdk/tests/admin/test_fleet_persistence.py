# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The app's fleet registry persists (factory SQLite) and is fail-closed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_app_fleet_registry_is_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.db import factory as _db_factory
    from sagewai.fleet.registry import PostgresFleetRegistry

    # factory.get_engine() is process-cached; a prior test may have cached an
    # engine for a different SAGEWAI_HOME. PostgresFleetRegistry() resolves the
    # engine eagerly in __init__, so reset the cache AFTER monkeypatching HOME and
    # BEFORE building the app, and again in finally so we don't pollute later tests.
    _db_factory.reset_engine()
    try:
        sf = AdminStateFile(path=tmp_path / "state.json")
        sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
        app = create_admin_serve_app(sf)
        # The wired registry is the SQLAlchemy (persistent) one, not in-memory.
        assert isinstance(app.state.fleet_registry, PostgresFleetRegistry)
        token = sf.validate_login("a@b.com", "pw123456")["access_token"]
        # `with TestClient(app) as c:` is REQUIRED — Starlette 1.2 only enters the
        # lifespan (which eagerly inits the registry) inside the context manager.
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {token}"})
            reg = c.post("/api/v1/fleet/register", json={"name": "w", "models": ["gpt-4o"]})
            assert reg.status_code == 201
        # A second app on the same cached file-backed engine sees the worker (durable).
        app2 = create_admin_serve_app(AdminStateFile(path=tmp_path / "state.json"))
        with TestClient(app2) as listed:
            listed.headers.update({"Authorization": f"Bearer {token}"})
            workers = listed.get("/api/v1/fleet/workers").json()["workers"]
        assert any(w["name"] == "w" for w in workers)
    finally:
        _db_factory.reset_engine()  # clear our engine so later tests re-resolve
