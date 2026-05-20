# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
from sagewai.tools import factory, registry


def _stub_creds(*, project_id, kind, id):
    return {"GITHUB_TOKEN": "ghp_x"}


@pytest.mark.asyncio
async def test_factory_builds_callables_for_seed_entries(monkeypatch):
    registry._reset()
    registry.load()

    async def fake_http_run(entry, *, operation, inputs, project_id, get_credentials):
        return {"echo": (entry.id, operation, inputs)}

    from sagewai.tools import executors
    monkeypatch.setitem(executors._REGISTRY, "http", fake_http_run)

    callables = factory.build_callables(project_id="p1", get_credentials=_stub_creds)
    assert "github" in callables
    assert "fetch_url" in callables
    out = await callables["github"]({"_operation": "get_repo", "owner": "o", "repo": "r"})
    assert out["echo"] == ("github", "get_repo", {"owner": "o", "repo": "r"})


@pytest.mark.asyncio
async def test_factory_callable_strips_underscore_operation_field(monkeypatch):
    registry._reset()
    registry.load()

    async def fake_sdk_run(entry, *, operation, inputs, project_id, get_credentials):
        return {"op": operation, "kept": inputs}

    from sagewai.tools import executors
    monkeypatch.setitem(executors._REGISTRY, "sdk", fake_sdk_run)

    callables = factory.build_callables(project_id="p1", get_credentials=_stub_creds)
    out = await callables["fetch_url"]({"_operation": "anything", "url": "https://x"})
    assert "_operation" not in out["kept"]
    assert out["kept"] == {"url": "https://x"}
    assert out["op"] == "anything"
