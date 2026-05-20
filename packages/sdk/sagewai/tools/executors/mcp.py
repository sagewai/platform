# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: mcp`` executor — MCP client over stdio or HTTP.

The real client wiring lands in sub-project 2; this module ships a stub
``_open_client`` so the seed test passes via monkeypatch.
"""
from __future__ import annotations

from typing import Any, Callable

from sagewai.tools.registry import CatalogEntry


def _open_client(server_ref: str):
    raise NotImplementedError("MCP client wiring lands in sub-project 2")


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., Any],
) -> dict[str, Any]:
    cfg = entry.exec_["mcp"]
    tool_name = operation or cfg.get("tool_name")
    if tool_name is None:
        raise ValueError("mcp executor: no tool_name and no operation passed")
    client = _open_client(cfg["server_ref"])
    try:
        return await client.call_tool(tool_name, inputs)
    finally:
        await client.close()
