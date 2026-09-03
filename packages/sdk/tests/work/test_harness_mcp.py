# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from __future__ import annotations

from typing import Any

import pytest

from sagewai.connections.store import ConnectionStore
from sagewai.models.tool import ToolSpec
from sagewai.work.harness_mcp import mcp_connection_resolver


class _Credentials:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], tuple[str, ...], dict[str, Any] | None]] = []

    def decrypt(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        connection_credentials_backend: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.calls.append(
            (protocol_data, sensitive_field_paths, connection_credentials_backend)
        )
        return {**protocol_data, "credentials": {"GITHUB_TOKEN": "ghp-secret"}}


@pytest.mark.asyncio
async def test_mcp_connection_resolver_uses_registered_project_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = ConnectionStore(tmp_path / "connections.json")
    store.create(
        protocol="mcp",
        project_id="project-a",
        display_name="GitHub",
        tags=[],
        protocol_data={
            "server_ref": "github",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
            "credentials": {"GITHUB_TOKEN": "encrypted"},
        },
    )
    credentials = _Credentials()
    clients = []

    class FakeMCPClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False
            clients.append(self)

        async def __aenter__(self):
            self.tools = [
                ToolSpec(
                    name="search_repositories",
                    description="search repositories",
                    parameters={},
                    handler=lambda **_kwargs: None,
                )
            ]
            return self

        async def __aexit__(self, *exc) -> None:
            self.closed = True

        async def list_tools(self):
            return list(self.tools)

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            return {"name": name, "arguments": arguments}

    monkeypatch.setattr("sagewai.connections.protocols.mcp.MCPClient", FakeMCPClient)

    resolver = mcp_connection_resolver(
        project_id="project-a",
        connection_store=store,
        credentials=credentials,
    )
    session = await resolver("github")

    assert [tool.name for tool in await session.list_tools()] == ["search_repositories"]
    assert await session.call("search_repositories", {"query": "sagewai"}) == {
        "name": "search_repositories",
        "arguments": {"query": "sagewai"},
    }
    assert clients[0].kwargs["env"]["GITHUB_TOKEN"] == "ghp-secret"
    assert credentials.calls[0][1] == ("credentials.GITHUB_TOKEN",)

    with pytest.raises(KeyError):
        await resolver("unknown")
    with pytest.raises(KeyError):
        await resolver("slack")
    project_b_resolver = mcp_connection_resolver(
        project_id="project-b",
        connection_store=store,
        credentials=credentials,
    )
    with pytest.raises(KeyError):
        await project_b_resolver("github")

    await session.close()
    assert clients[0].closed is True
