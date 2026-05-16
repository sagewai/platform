# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool discovery — OpenAI function-calling compatible tool listing.

Returns tools in the format expected by OpenAI's function calling,
Claude's tool_use, and MCP tool schemas.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from sagewai.models.tool import ToolSpec


def create_tool_discovery_router(tools: list[ToolSpec]) -> APIRouter:
    """Create a router that exposes agent tools in OpenAI function calling format.

    Args:
        tools: List of ToolSpec definitions to expose.

    Returns:
        FastAPI router with ``GET /api/v1/tools`` and ``GET /api/v1/tools/{name}``.
    """
    router = APIRouter(prefix="/api/v1", tags=["tools"])

    tools_by_name = {t.name: t for t in tools}

    def _to_openai_format(spec: ToolSpec) -> dict:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }

    @router.get("/tools")
    async def list_tools():
        return {"tools": [_to_openai_format(t) for t in tools]}

    @router.get("/tools/{name}")
    async def get_tool(name: str):
        spec = tools_by_name.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"Tool not found: {name}")
        return _to_openai_format(spec)

    return router
