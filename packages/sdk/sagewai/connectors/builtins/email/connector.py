# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Email connector spec."""

import hashlib
import hmac

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class EmailConnector(ConnectorSpec):
    """Email (SendGrid) connector."""

    name: str = "email"
    display_name: str = "Email (SendGrid)"
    category: str = "communication"
    description: str = (
        "Send emails, manage contacts, and track deliveries via SendGrid."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="api_key",
            label="API Key",
            env_var="SENDGRID_API_KEY",
            hint="From app.sendgrid.com",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.email.server",
    ]
    docs_url: str = "https://docs.sendgrid.com"
    supports_webhook: bool = True
    supports_poller: bool = True

    async def verify_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
        credentials: dict[str, str],
    ) -> bool:
        """Verify email webhook signature using HMAC-SHA256."""
        try:
            webhook_secret = credentials.get("webhook_secret", "")
            if not webhook_secret:
                return False

            signature = headers.get("x-webhook-signature", "")
            if not signature:
                return False

            computed = hmac.new(
                webhook_secret.encode(),
                request_body,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(computed, signature)
        except Exception:
            return False
