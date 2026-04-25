# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""End-to-end: admin override via API → resolved requirements at enqueue time."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    from fastapi import FastAPI

    from sagewai.admin import sandbox_routes

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"projects": [], "agents": []}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    app = FastAPI()
    sandbox_routes.register(app, store=None)
    return TestClient(app)


@pytest.mark.asyncio
async def test_admin_override_overrides_blueprint_at_enqueue(admin_client):
    """PUT admin override → resolve_agent_requirements returns override values."""
    from sagewai.sandbox.models import (
        NetworkPolicy,
        SandboxImageVariant,
        SandboxMode,
    )
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        resolve_agent_requirements,
    )

    # Set admin override via API
    res = admin_client.put(
        "/api/v1/admin/agents/writer/sandbox-requirements",
        json={
            "sandbox_mode": "per_run",
            "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
            "network_policy": "full",
            "required_secret_scopes": [],
        },
    )
    assert res.status_code == 200, res.text

    # Resolve as a workflow.enqueue() would
    blueprint_reqs = SandboxRequirements(
        sandbox_mode=SandboxMode.NONE,
        image="ghcr.io/sagewai/sandbox-base:1.0",
        variant=SandboxImageVariant.BASE,
        network_policy=NetworkPolicy.NONE,
    )
    resolved = await resolve_agent_requirements(
        "writer", blueprint_requirements=blueprint_reqs
    )

    assert resolved is not None
    assert resolved.sandbox_mode is SandboxMode.PER_RUN
    assert resolved.image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert resolved.network_policy is NetworkPolicy.FULL


@pytest.mark.asyncio
async def test_admin_override_cleared_falls_back_to_blueprint(admin_client):
    """PUT then DELETE → resolve returns Blueprint."""
    from sagewai.sandbox.models import (
        NetworkPolicy,
        SandboxImageVariant,
        SandboxMode,
    )
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        resolve_agent_requirements,
    )

    payload = {
        "sandbox_mode": "per_run",
        "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
        "network_policy": "full",
        "required_secret_scopes": [],
    }
    admin_client.put("/api/v1/admin/agents/writer/sandbox-requirements", json=payload)
    admin_client.delete("/api/v1/admin/agents/writer/sandbox-requirements")

    blueprint_reqs = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_TOOL,
        image="ghcr.io/sagewai/sandbox-base:1.0",
        variant=SandboxImageVariant.BASE,
        network_policy=NetworkPolicy.NONE,
    )
    resolved = await resolve_agent_requirements(
        "writer", blueprint_requirements=blueprint_reqs
    )

    # Admin override cleared → Blueprint value returned as-is
    assert resolved is blueprint_reqs


@pytest.mark.asyncio
async def test_project_default_inherited_when_no_agent_override(admin_client):
    """Set project default via API → no agent override → resolve_requirements returns project values."""
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import SandboxRequirements, resolve_requirements

    admin_client.put(
        "/api/v1/admin/projects/acme/sandbox-defaults",
        json={
            "sandbox_mode": "per_run",
            "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
            "network_policy": "full",
            "required_secret_scopes": [],
        },
    )

    # Read project defaults back from admin-state (as enqueue would)
    state = AdminStateFile()
    proj = state.get_project("acme")
    assert proj is not None, "project 'acme' should have been created by PUT"
    defaults_dict = proj["default_sandbox_requirements"]
    project_defaults = SandboxRequirements(
        sandbox_mode=SandboxMode(defaults_dict["sandbox_mode"]),
        image=defaults_dict["image"],
        variant=image_manifest.lookup_variant(defaults_dict["image"]),
        network_policy=NetworkPolicy(defaults_dict["network_policy"]),
    )

    resolved = await resolve_requirements(
        agent_requirements=None,
        project_defaults=project_defaults,
    )
    assert resolved.sandbox_mode is SandboxMode.PER_RUN
    assert resolved.network_policy is NetworkPolicy.FULL
