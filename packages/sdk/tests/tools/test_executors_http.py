# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import httpx
import pytest
import respx
from sagewai.tools import registry
from sagewai.tools.executors import http as http_exec


def _creds(value):
    def _get(*, project_id, kind, id):
        return {"GITHUB_TOKEN": value}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_sends_request_with_auth_header():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")

    route = respx.get("https://api.github.com/repos/octocat/hello-world").respond(
        200, json={"id": 1, "name": "hello-world", "full_name": "octocat/hello-world"}
    )
    out = await http_exec.run(
        entry,
        operation="get_repo",
        inputs={"owner": "octocat", "repo": "hello-world"},
        project_id="p1",
        get_credentials=_creds("ghp_abc"),
    )
    assert out["name"] == "hello-world"
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_abc"


@pytest.mark.asyncio
async def test_http_executor_rejects_bad_input_schema():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    with pytest.raises(http_exec.InputValidationError):
        await http_exec.run(
            entry,
            operation="get_repo",
            inputs={"owner": "octocat"},  # missing repo
            project_id="p1",
            get_credentials=_creds("ghp_abc"),
        )


@pytest.mark.asyncio
async def test_http_executor_raises_on_unknown_operation():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    with pytest.raises(http_exec.UnknownOperationError):
        await http_exec.run(
            entry,
            operation="nope",
            inputs={},
            project_id="p1",
            get_credentials=_creds("ghp_abc"),
        )
