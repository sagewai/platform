# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Slack connector spec."""

import hashlib
import hmac

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class SlackConnector(ConnectorSpec):
    """Slack workspace connector."""

    name: str = "slack"
    display_name: str = "Slack"
    category: str = "communication"
    description: str = (
        "Send messages, manage channels, and interact with Slack workspaces."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="bot_token",
            label="Bot Token",
            env_var="SLACK_BOT_TOKEN",
            hint="From api.slack.com/apps",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.slack.server",
    ]
    docs_url: str = "https://api.slack.com/apps"
    supports_webhook: bool = True
    supports_listener: bool = True
    supports_poller: bool = True

    async def verify_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
        credentials: dict[str, str],
    ) -> bool:
        """Verify Slack webhook signature using HMAC-SHA256."""
        try:
            signing_secret = credentials["signing_secret"]
            timestamp = headers["x-slack-request-timestamp"]
            signature = headers["x-slack-signature"]

            base_string = f"v0:{timestamp}:{request_body.decode()}"
            computed = (
                "v0="
                + hmac.new(
                    signing_secret.encode(),
                    base_string.encode(),
                    hashlib.sha256,
                ).hexdigest()
            )
            return hmac.compare_digest(computed, signature)
        except Exception:
            return False
