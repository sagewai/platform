# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Project scoping for the saved workflow registry routes."""

from fastapi.testclient import TestClient

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


def _client(tmp_path):
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    c = TestClient(create_admin_serve_app(sf))
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_workflow_registry_lists_only_selected_project(tmp_path):
    c = _client(tmp_path)

    a = c.post(
        "/api/v1/workflow-registry",
        headers={"X-Project-ID": "p1"},
        json={"name": "wf-p1", "yaml_content": "name: wf-p1\nworkflow: []"},
    )
    b = c.post(
        "/api/v1/workflow-registry",
        headers={"X-Project-ID": "p2"},
        json={"name": "wf-p2", "yaml_content": "name: wf-p2\nworkflow: []"},
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    assert a.json()["project_id"] == "p1"
    assert b.json()["project_id"] == "p2"

    res = c.get("/api/v1/workflow-registry", headers={"X-Project-ID": "p1"})
    assert res.status_code == 200, res.text
    names = {wf["name"] for wf in res.json()["items"]}
    assert names == {"wf-p1"}


def test_workflow_registry_delete_cannot_cross_project(tmp_path):
    c = _client(tmp_path)
    created = c.post(
        "/api/v1/workflow-registry",
        headers={"X-Project-ID": "p2"},
        json={"name": "wf-p2", "yaml_content": "name: wf-p2\nworkflow: []"},
    ).json()

    res = c.delete(
        f"/api/v1/workflow-registry/{created['id']}",
        headers={"X-Project-ID": "p1"},
    )
    assert res.status_code == 404

    still_there = c.get("/api/v1/workflow-registry", headers={"X-Project-ID": "p2"})
    assert [wf["id"] for wf in still_there.json()["items"]] == [created["id"]]
