# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin /sealed/status with Sealed-ii backends registered."""
from __future__ import annotations

import json
from pathlib import Path

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


def test_status_route_mounts_without_vault_config(tmp_path: Path) -> None:
    state_path = tmp_path / "admin-state.json"
    state_path.write_text(json.dumps({"setup_complete": True, "admin": {}}))
    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert any("/sealed/status" in r for r in routes)


def test_status_route_mounts_with_vault_disabled(tmp_path: Path) -> None:
    state_path = tmp_path / "admin-state.json"
    state_path.write_text(
        json.dumps({
            "setup_complete": True,
            "admin": {},
            "sealed": {"vault": {"enabled": False}},
        })
    )
    sf = AdminStateFile(path=state_path)
    create_admin_serve_app(sf)
    from sagewai.sealed.refs import list_registered_schemes
    assert "vault" not in list_registered_schemes()


def test_status_response_includes_backends_map(tmp_path: Path) -> None:
    """`/sealed/status` returns the new `backends` map with builtin entry."""
    from fastapi.testclient import TestClient

    state_path = tmp_path / "admin-state.json"
    state_path.write_text(
        json.dumps({
            "setup_complete": True,
            "admin": {"email": "x@y.z", "password_hash": "x", "role": "admin"},
            "active_tokens": ["test-token"],
            "sealed": {"vault": {"enabled": False}},
        })
    )
    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    client = TestClient(app)
    resp = client.get(
        "/api/v1/admin/sealed/status",
        cookies={"sagewai_auth": "test-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "backends" in body
    assert "builtin" in body["backends"]
    assert body["backends"]["builtin"]["enabled"] is True
    if "vault" in body["backends"]:
        assert body["backends"]["vault"]["enabled"] is False
