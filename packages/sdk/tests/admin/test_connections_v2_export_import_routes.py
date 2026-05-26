# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the admin v2 connections export + import routes."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml as pyyaml
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin import connections_v2_routes
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.protocols import oauth2 as oauth2_module
from sagewai.oauth import pending_auth


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def master_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key)
    return key


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch) -> Path:
    sp = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(sp))
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    return sp


@pytest.fixture
def sf(state_path: Path) -> AdminStateFile:
    sf = AdminStateFile(state_path)
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter22",
    )
    return sf


@pytest.fixture
def token(sf: AdminStateFile) -> str:
    result = sf.validate_login("admin@example.com", "hunter22")
    assert result is not None
    return result["access_token"]


@pytest.fixture
def client(sf: AdminStateFile, token: str, master_key: str) -> TestClient:
    """TestClient with a logged-in cookie."""
    pending_auth.reset_default_store_for_tests()
    oauth2_module._test_inject_context(None)
    app = FastAPI()
    connections_v2_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    yield tc
    oauth2_module._test_inject_context(None)


# ── export route ─────────────────────────────────────────────────────


def test_export_returns_yaml_content_type(client: TestClient):
    resp = client.get(
        "/api/v1/admin/connections/export?project_id=test",
        headers={"X-Project-ID": "test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/yaml")


def test_export_returns_attachment_disposition(client: TestClient):
    resp = client.get(
        "/api/v1/admin/connections/export?project_id=test",
        headers={"X-Project-ID": "test"},
    )
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "filename=" in resp.headers.get("content-disposition", "")


def test_export_yaml_body_is_parseable(client: TestClient):
    resp = client.get(
        "/api/v1/admin/connections/export?project_id=test",
        headers={"X-Project-ID": "test"},
    )
    body = pyyaml.safe_load(resp.text)
    assert body["version"] == 1
    assert body["secrets_mode"] == "redacted"


def test_export_invalid_secrets_mode_returns_400(client: TestClient):
    resp = client.get(
        "/api/v1/admin/connections/export?project_id=test&secrets=bogus",
        headers={"X-Project-ID": "test"},
    )
    assert resp.status_code == 400


def test_export_filter_by_protocol(client: TestClient):
    # Seed a connection via the existing CRUD route
    create = client.post(
        "/api/v1/admin/connections/",
        headers={"X-Project-ID": "test"},
        json={
            "protocol": "http",
            "display_name": "x-http",
            "tags": [],
            "credentials_backend": {"kind": "local"},
            "protocol_data": {"base_url": "https://a.com", "auth": {"kind": "none"}},
        },
    )
    assert create.status_code == 200, create.text
    resp = client.get(
        "/api/v1/admin/connections/export?project_id=test&protocol=http",
        headers={"X-Project-ID": "test"},
    )
    assert resp.status_code == 200, resp.text
    body = pyyaml.safe_load(resp.text)
    assert len(body["connections"]) == 1
    assert body["connections"][0]["protocol"] == "http"


# ── import route ─────────────────────────────────────────────────────


_BASIC_YAML = dedent("""
    version: 1
    secrets_mode: redacted
    connections:
      - protocol: http
        display_name: imported-http
        tags: []
        credentials_backend:
          kind: local
        is_default: true
        protocol_data:
          base_url: https://a.com
          auth:
            kind: none
""").strip()


def test_import_yaml_body_creates_connection(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test",
        headers={
            "X-Project-ID": "test",
            "Content-Type": "application/yaml",
        },
        content=_BASIC_YAML,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["display_name"] == "imported-http"

    # Verify persisted
    list_resp = client.get(
        "/api/v1/admin/connections/", headers={"X-Project-ID": "test"}
    )
    names = {c["display_name"] for c in list_resp.json()}
    assert "imported-http" in names


def test_import_multipart_file_upload(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test",
        headers={"X-Project-ID": "test"},
        files={"file": ("export.yaml", _BASIC_YAML, "application/yaml")},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["created"]) == 1


def test_import_dry_run_does_not_persist(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test&dry_run=true",
        headers={
            "X-Project-ID": "test",
            "Content-Type": "application/yaml",
        },
        content=_BASIC_YAML,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dry_run"] is True
    assert len(data["created"]) == 1

    list_resp = client.get(
        "/api/v1/admin/connections/", headers={"X-Project-ID": "test"}
    )
    names = {c["display_name"] for c in list_resp.json()}
    assert "imported-http" not in names


def test_import_invalid_yaml_returns_400(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test",
        headers={
            "X-Project-ID": "test",
            "Content-Type": "application/yaml",
        },
        content="not: valid: yaml: : :",
    )
    assert resp.status_code == 400


def test_import_create_only_collision_returns_400(client: TestClient):
    """Spec: create-only is all-or-nothing — any error means zero writes
    happened, so the HTTP response must be a 400 with the result body."""
    # First, create the existing row via a successful import.
    first = client.post(
        "/api/v1/admin/connections/import?project_id=test",
        headers={"X-Project-ID": "test", "Content-Type": "application/yaml"},
        content=_BASIC_YAML,
    )
    assert first.status_code == 200, first.text

    # Re-import the same YAML in create-only mode — must collide and 400.
    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test&mode=create-only",
        headers={"X-Project-ID": "test", "Content-Type": "application/yaml"},
        content=_BASIC_YAML,
    )
    assert resp.status_code == 400, resp.text
    data = resp.json()
    assert any(
        e["code"] == "import_display_name_collision" for e in data["errors"]
    ), data


def test_export_logs_exported_by_user(client: TestClient, caplog):
    """Spec audit-log: export event payload includes ``exported_by_user``."""
    import logging

    with caplog.at_level(logging.INFO, logger="sagewai.admin"):
        resp = client.get(
            "/api/v1/admin/connections/export?project_id=test",
            headers={"X-Project-ID": "test"},
        )
    assert resp.status_code == 200

    matching = [
        r for r in caplog.records
        if getattr(r, "event", None) == "connections.export.completed"
    ]
    assert matching, "no export-completed event found in logs"
    assert matching[0].exported_by_user == "admin@example.com"


def test_import_logs_imported_by_user(client: TestClient, caplog):
    """Spec audit-log: import event payload includes ``imported_by_user``."""
    import logging

    with caplog.at_level(logging.INFO, logger="sagewai.admin"):
        resp = client.post(
            "/api/v1/admin/connections/import?project_id=test",
            headers={"X-Project-ID": "test", "Content-Type": "application/yaml"},
            content=_BASIC_YAML,
        )
    assert resp.status_code == 200, resp.text

    matching = [
        r for r in caplog.records
        if getattr(r, "event", None) == "connections.import.completed"
    ]
    assert matching, "no import-completed event found in logs"
    assert matching[0].imported_by_user == "admin@example.com"


def test_import_upsert_collision_returns_200(client: TestClient):
    """Sanity: upsert with collisions still returns 200 — write succeeded."""
    first = client.post(
        "/api/v1/admin/connections/import?project_id=test",
        headers={"X-Project-ID": "test", "Content-Type": "application/yaml"},
        content=_BASIC_YAML,
    )
    assert first.status_code == 200, first.text

    resp = client.post(
        "/api/v1/admin/connections/import?project_id=test&mode=upsert",
        headers={"X-Project-ID": "test", "Content-Type": "application/yaml"},
        content=_BASIC_YAML,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["errors"] == []
    assert len(data["updated"]) == 1
