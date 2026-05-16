# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Backend tests for /api/v1/admin/.../sandbox-* endpoints (Plan 3b-i)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_state_factory(tmp_path, monkeypatch):
    """Returns a factory that writes admin-state.json with given content."""
    state_file = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    def _write(**kwargs):
        state_file.write_text(json.dumps(kwargs))
        return state_file
    return _write


@pytest.fixture
def admin_client(admin_state_factory):
    """Lightweight FastAPI test client wired with sandbox_routes only."""
    from fastapi import FastAPI

    from sagewai.admin import sandbox_routes

    app = FastAPI()
    sandbox_routes.register(app, store=None)
    return TestClient(app)


# ── project defaults ────────────────────────────────────────────────


def test_get_project_defaults_404_when_unset(admin_client, admin_state_factory):
    admin_state_factory(projects=[{"slug": "acme"}])
    res = admin_client.get("/api/v1/admin/projects/acme/sandbox-defaults")
    assert res.status_code == 404
    body = res.json()
    # detail field carries the structured error shape
    detail = body.get("detail", body)
    assert detail.get("scope") == "project"
    assert detail.get("id") == "acme"


def test_get_project_defaults_404_when_project_missing(admin_client, admin_state_factory):
    admin_state_factory(projects=[])
    res = admin_client.get("/api/v1/admin/projects/ghost/sandbox-defaults")
    assert res.status_code == 404


def test_put_project_defaults_creates_then_reads(admin_client, admin_state_factory):
    admin_state_factory(projects=[{"slug": "acme"}])
    payload = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
        "network_policy": "full",
        "required_secret_scopes": [],
    }
    res = admin_client.put("/api/v1/admin/projects/acme/sandbox-defaults", json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["sandbox_mode"] == "per_run"

    res = admin_client.get("/api/v1/admin/projects/acme/sandbox-defaults")
    assert res.status_code == 200
    body = res.json()
    assert body["sandbox_mode"] == "per_run"
    assert body["image"] == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert body["variant"] is None  # PINNED_DIGESTS empty in test → None
    assert body["network_policy"] == "full"


def test_put_project_defaults_rejects_invalid_mode(admin_client, admin_state_factory):
    admin_state_factory(projects=[{"slug": "acme"}])
    res = admin_client.put(
        "/api/v1/admin/projects/acme/sandbox-defaults",
        json={
            "sandbox_mode": "bogus",
            "image": "ghcr.io/sagewai/sandbox-base:1.0",
            "network_policy": "none",
            "required_secret_scopes": [],
        },
    )
    assert res.status_code == 422


def test_delete_project_defaults_clears_field(admin_client, admin_state_factory):
    admin_state_factory(projects=[{"slug": "acme"}])
    payload = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-base:1.0",
        "network_policy": "none",
        "required_secret_scopes": [],
    }
    admin_client.put("/api/v1/admin/projects/acme/sandbox-defaults", json=payload)

    del_res = admin_client.delete("/api/v1/admin/projects/acme/sandbox-defaults")
    assert del_res.status_code == 200
    assert del_res.json()["cleared"] is True

    get_res = admin_client.get("/api/v1/admin/projects/acme/sandbox-defaults")
    assert get_res.status_code == 404


def test_delete_project_defaults_idempotent(admin_client, admin_state_factory):
    admin_state_factory(projects=[{"slug": "acme"}])
    res = admin_client.delete("/api/v1/admin/projects/acme/sandbox-defaults")
    assert res.status_code in (200, 204)


# ── agent overrides ────────────────────────────────────────────────


def test_get_agent_overrides_404_when_unset(admin_client, admin_state_factory):
    admin_state_factory(agents=[{"name": "writer", "model": "gpt-4o"}])
    res = admin_client.get("/api/v1/admin/agents/writer/sandbox-requirements")
    assert res.status_code == 404
    body = res.json()
    detail = body.get("detail", body)
    assert detail.get("scope") == "agent"


def test_put_agent_overrides_creates_admin_state_entry(admin_client, admin_state_factory):
    admin_state_factory(agents=[])
    payload = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-general:0.1.5",
        "network_policy": "full",
        "required_secret_scopes": [],
    }
    res = admin_client.put("/api/v1/admin/agents/writer/sandbox-requirements", json=payload)
    assert res.status_code == 200, res.text


def test_put_agent_overrides_existing_agent_preserves_fields(admin_client, admin_state_factory):
    admin_state_factory(
        agents=[{"name": "writer", "model": "gpt-4o", "system_prompt": "You are helpful."}]
    )
    payload = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-general:0.1.5",
        "network_policy": "full",
        "required_secret_scopes": [],
    }
    admin_client.put("/api/v1/admin/agents/writer/sandbox-requirements", json=payload)

    import json
    import os
    state = json.loads(open(os.environ["SAGEWAI_ADMIN_STATE_FILE"]).read())
    writer = next(a for a in state["agents"] if a["name"] == "writer")
    assert writer["model"] == "gpt-4o"
    assert writer["system_prompt"] == "You are helpful."
    assert writer["sandbox_requirements_override"]["sandbox_mode"] == "per_run"


def test_delete_agent_overrides_clears_only_field(admin_client, admin_state_factory):
    admin_state_factory(
        agents=[{
            "name": "writer",
            "model": "gpt-4o",
            "sandbox_requirements_override": {
                "sandbox_mode": "per_run",
                "image": "ghcr.io/sagewai/sandbox-general:0.1.5",
                "network_policy": "full",
                "required_secret_scopes": [],
            },
        }]
    )
    res = admin_client.delete("/api/v1/admin/agents/writer/sandbox-requirements")
    assert res.status_code == 200
    assert res.json()["cleared"] is True

    get_res = admin_client.get("/api/v1/admin/agents/writer/sandbox-requirements")
    assert get_res.status_code == 404


def test_routes_handle_url_encoded_names(admin_client, admin_state_factory):
    """Agent name 'my agent' (with space) URL-encoded resolves correctly."""
    admin_state_factory(agents=[])
    payload = {
        "sandbox_mode": "none",
        "image": "ghcr.io/sagewai/sandbox-base:1.0",
        "network_policy": "none",
        "required_secret_scopes": [],
    }
    res = admin_client.put("/api/v1/admin/agents/my%20agent/sandbox-requirements", json=payload)
    assert res.status_code == 200
    res = admin_client.get("/api/v1/admin/agents/my%20agent/sandbox-requirements")
    assert res.status_code == 200


# ── preview endpoint ───────────────────────────────────────────────


def test_preview_endpoint_resolves_cascade(admin_client, admin_state_factory):
    """Project default + no agent override → preview reflects project values + origins."""
    admin_state_factory(
        projects=[{
            "slug": "acme",
            "default_sandbox_requirements": {
                "sandbox_mode": "per_tool",
                "image": "ghcr.io/sagewai/sandbox-base:1.0",
                "network_policy": "none",
                "required_secret_scopes": [],
            },
        }],
        agents=[],
    )
    res = admin_client.get("/api/v1/admin/sandbox/preview?project=acme")
    assert res.status_code == 200
    body = res.json()
    assert body["sandbox_mode"]["value"] == "per_tool"
    assert body["sandbox_mode"]["origin"] == "project_default"
    assert body["resolved"]["sandbox_mode"] == "per_tool"


def test_preview_endpoint_admin_override_origin(admin_client, admin_state_factory):
    """Agent admin override takes precedence; origin tagged admin_override."""
    admin_state_factory(
        projects=[{
            "slug": "acme",
            "default_sandbox_requirements": {
                "sandbox_mode": "per_tool",
                "image": "ghcr.io/sagewai/sandbox-base:1.0",
                "network_policy": "none",
                "required_secret_scopes": [],
            },
        }],
        agents=[{
            "name": "writer",
            "sandbox_requirements_override": {
                "sandbox_mode": "per_run",
                "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
                "network_policy": "full",
                "required_secret_scopes": [],
            },
        }],
    )
    res = admin_client.get("/api/v1/admin/sandbox/preview?project=acme&agent=writer")
    assert res.status_code == 200
    body = res.json()
    assert body["sandbox_mode"]["value"] == "per_run"
    assert body["sandbox_mode"]["origin"] == "admin_override"
    assert body["image"]["origin"] == "admin_override"


def test_preview_endpoint_with_draft_overrides(admin_client, admin_state_factory):
    """draft_* query params override stored config; origin tagged 'explicit'."""
    admin_state_factory(projects=[], agents=[])
    res = admin_client.get(
        "/api/v1/admin/sandbox/preview"
        "?project=acme"
        "&draft_mode=per_run"
        "&draft_image=ghcr.io/sagewai/sandbox-ml:0.1.5"
        "&draft_network_policy=full"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sandbox_mode"]["value"] == "per_run"
    assert body["sandbox_mode"]["origin"] == "explicit"


def test_preview_endpoint_byo_returns_null_variant(admin_client, admin_state_factory):
    admin_state_factory(
        projects=[{
            "slug": "acme",
            "default_sandbox_requirements": {
                "sandbox_mode": "per_run",
                "image": "ghcr.io/acme/custom:1.0",   # BYO
                "network_policy": "full",
                "required_secret_scopes": [],
            },
        }],
    )
    res = admin_client.get("/api/v1/admin/sandbox/preview?project=acme")
    assert res.status_code == 200
    assert res.json()["variant"] is None
