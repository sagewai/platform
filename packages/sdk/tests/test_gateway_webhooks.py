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
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sagewai.gateway.webhooks import WebhookRouter
from sagewai.connectors.base import AuthType, ConnectorSpec


@pytest.fixture
def webhook_router():
    return WebhookRouter()


def test_register_and_get_router(webhook_router):
    router = webhook_router.get_fastapi_router()
    assert router is not None


def test_webhook_rejects_unverified():
    """Webhooks with failed signature verification return 401."""
    wr = WebhookRouter()

    spec = ConnectorSpec(
        name="test", display_name="Test", category="test",
        description="Test", auth_type=AuthType.NONE,
        auth_fields=[], mcp_command=["echo"],
        supports_webhook=True,
    )
    handler = AsyncMock()
    wr.register_connector_webhook("test", spec, handler, credentials={})

    app = FastAPI()
    app.include_router(wr.get_fastapi_router())
    client = TestClient(app)

    resp = client.post("/webhooks/test", json={"event": "test"})
    # Default verify_webhook returns False → 401
    assert resp.status_code == 401
    handler.assert_not_called()
