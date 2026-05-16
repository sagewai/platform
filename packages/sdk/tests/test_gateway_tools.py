# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for tool discovery endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.gateway.tools import create_tool_discovery_router
from sagewai.models.tool import ToolSpec


@pytest.fixture
def app():
    tools = [
        ToolSpec(
            name="search_web",
            description="Search the web for information",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            name="get_weather",
            description="Get current weather for a location",
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                },
                "required": ["location"],
            },
        ),
    ]
    router = create_tool_discovery_router(tools)
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_list_tools_returns_openai_format(client):
    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tools"]) == 2
    tool = data["tools"][0]
    assert tool["type"] == "function"
    assert "function" in tool
    assert tool["function"]["name"] == "search_web"
    assert "parameters" in tool["function"]


def test_list_tools_has_descriptions(client):
    resp = client.get("/api/v1/tools")
    data = resp.json()
    names = {t["function"]["name"] for t in data["tools"]}
    assert names == {"search_web", "get_weather"}


def test_get_single_tool(client):
    resp = client.get("/api/v1/tools/search_web")
    assert resp.status_code == 200
    data = resp.json()
    assert data["function"]["name"] == "search_web"


def test_get_nonexistent_tool(client):
    resp = client.get("/api/v1/tools/nonexistent")
    assert resp.status_code == 404
