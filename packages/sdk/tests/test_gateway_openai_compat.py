# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
# packages/sagewai/tests/test_gateway_openai_compat.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sagewai.gateway.openai_compat import create_openai_compat_router


@pytest.fixture
def app():
    mock_agent = MagicMock()
    mock_agent.config.name = "test-agent"
    mock_agent.config.model = "gpt-4o"
    mock_agent.chat = AsyncMock(return_value="Hello!")

    app = FastAPI()
    router = create_openai_compat_router(agents={"test-agent": mock_agent})
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_list_models(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "test-agent"


def test_chat_completions(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "test-agent",
        "messages": [{"role": "user", "content": "Hi"}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello!"


def test_chat_completions_unknown_model(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "nonexistent",
        "messages": [{"role": "user", "content": "Hi"}],
    })
    assert resp.status_code == 404
