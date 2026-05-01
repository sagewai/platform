# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for connector base models."""

import pytest
from pydantic import ValidationError

from sagewai.connectors.base import (
    AuthField,
    AuthType,
    ConnectorSpec,
    ConnectorStatus,
    HealthStatus,
    TokenSet,
)


def test_auth_type_values():
    assert AuthType.ENV_KEY == "env_key"
    assert AuthType.API_KEY == "api_key"
    assert AuthType.OAUTH2 == "oauth2"
    assert AuthType.NONE == "none"


def test_auth_field_creation():
    field = AuthField(
        key="bot_token",
        label="Bot Token",
        env_var="SLACK_BOT_TOKEN",
        hint="From api.slack.com",
    )
    assert field.key == "bot_token"
    assert field.secret is True  # default


def test_auth_field_non_secret():
    field = AuthField(key="host", label="Host", env_var="HOST", secret=False)
    assert field.secret is False


def test_health_status_defaults():
    hs = HealthStatus(status="healthy")
    assert hs.latency_ms is None
    assert hs.tool_count is None


def test_token_set():
    ts = TokenSet(access_token="abc123")
    assert ts.token_type == "Bearer"
    assert ts.refresh_token is None
    assert ts.expires_at is None


def test_connector_status():
    cs = ConnectorStatus(
        connector_name="slack",
        status="configured",
        has_credentials=True,
    )
    assert cs.env_vars_set == {}


def test_connector_spec_requires_fields():
    """ConnectorSpec is a BaseModel — missing required fields raise ValidationError."""
    with pytest.raises(ValidationError):
        ConnectorSpec()


def test_connector_spec_validate_credentials():
    spec = ConnectorSpec(
        name="test",
        display_name="Test",
        category="test",
        description="A test connector",
        auth_type=AuthType.API_KEY,
        auth_fields=[AuthField(key="api_key", label="API Key", env_var="TEST_KEY")],
        mcp_command=["echo"],
    )
    errors = spec.validate_credentials({})
    assert len(errors) == 1
    assert "API Key" in errors[0]

    errors = spec.validate_credentials({"api_key": "sk-123"})
    assert errors == []


@pytest.mark.asyncio
async def test_connector_spec_verify_webhook_default_rejects():
    """Default verify_webhook is fail-closed."""
    spec = ConnectorSpec(
        name="test",
        display_name="Test",
        category="test",
        description="Test",
        auth_type=AuthType.NONE,
        auth_fields=[],
        mcp_command=["echo"],
    )
    result = await spec.verify_webhook(b"body", {}, {})
    assert result is False
