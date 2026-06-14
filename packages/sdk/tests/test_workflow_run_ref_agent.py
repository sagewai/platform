# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""POST /workflows/run must resolve a registered-agent reference to its model.

Regression: a workflow step that references a registered agent (e.g. a local
ollama agent built in the Workflow Builder) carries no inline ``model`` in the
generated YAML — just ``ref: <name>``. The executor used to read ``model``
straight from the YAML def and fall back to ``gpt-4o-mini``, so the run routed
to OpenAI ("Missing credentials") instead of the agent's configured model.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    from sagewai.admin.state_file import AdminStateFile

    path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    # A registered agent with a non-default (local) model.
    sf.create_agent(
        {
            "name": "ollama-agent",
            "model": "ollama/qwen2.5:14b",
            "system_prompt": "You are a test agent.",
            "strategy": "react",
        }
    )

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "default_admin_state_path", lambda: path)
    return path


@pytest.fixture
async def client(state_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_workflow_run_uses_ref_agent_model_not_openai_default(client, monkeypatch):
    # Capture the model litellm is asked to run, then fail fast so the executor
    # doesn't actually reach out to a model server.
    captured: dict[str, str] = {}

    async def fake_acompletion(**kwargs):
        captured["model"] = kwargs.get("model", "")
        raise RuntimeError("offline test — no model server")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    # The Workflow Builder emits a `ref` agent with no inline model.
    yaml_str = (
        "name: ref-wf\n"
        "agents:\n"
        "  ollama-agent:\n"
        "    ref: ollama-agent\n"
        "workflow:\n"
        "  type: sequential\n"
        "  steps:\n"
        "    - agent: ollama-agent\n"
    )

    async with client.stream(
        "POST", "/workflows/run", json={"yaml": yaml_str, "message": "hi"}
    ) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass  # drain the SSE stream to completion

    assert captured.get("model") == "ollama/qwen2.5:14b", (
        f"ref agent resolved to {captured.get('model')!r} — expected the "
        "registered agent's model, not the gpt-4o-mini OpenAI default"
    )
