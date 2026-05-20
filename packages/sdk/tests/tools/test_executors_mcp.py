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
from sagewai.tools import registry
from sagewai.tools.executors import mcp as mcp_exec


def _noop_creds(*, project_id, kind, id):
    return {}


class _StubClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, inputs):
        self.calls.append((name, inputs))
        return {"content": [{"type": "text", "text": "hello"}]}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_mcp_executor_invokes_named_tool(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr(mcp_exec, "_open_client", lambda server_ref: stub)

    registry._reset()
    registry.load()
    entry = registry.lookup("filesystem_mcp")
    out = await mcp_exec.run(
        entry, operation=None, inputs={"path": "/tmp/x"},
        project_id="p1", get_credentials=_noop_creds,
    )
    assert stub.calls == [("read_file", {"path": "/tmp/x"})]
    assert "content" in out


@pytest.mark.asyncio
async def test_mcp_executor_overrides_tool_name_with_operation(monkeypatch):
    stub = _StubClient()
    monkeypatch.setattr(mcp_exec, "_open_client", lambda server_ref: stub)
    registry._reset()
    registry.load()
    entry = registry.lookup("filesystem_mcp")
    await mcp_exec.run(
        entry, operation="list_dir", inputs={"path": "/"},
        project_id="p1", get_credentials=_noop_creds,
    )
    assert stub.calls == [("list_dir", {"path": "/"})]
