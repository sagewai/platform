# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Payments connector spec."""

import hashlib
import hmac

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class PaymentsConnector(ConnectorSpec):
    """Stripe payments connector."""

    name: str = "payments"
    display_name: str = "Stripe"
    category: str = "finance"
    description: str = (
        "Create charges, manage subscriptions, and handle payment intents via Stripe."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="secret_key",
            label="Secret Key",
            env_var="STRIPE_SECRET_KEY",
            hint="From dashboard.stripe.com",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.payments.server",
    ]
    docs_url: str = "https://stripe.com/docs"
    supports_webhook: bool = True

    async def verify_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
        credentials: dict[str, str],
    ) -> bool:
        """Verify Stripe webhook signature using HMAC-SHA256."""
        try:
            webhook_secret = credentials["webhook_secret"]
            sig_header = headers["stripe-signature"]

            # Parse t=TIMESTAMP,v1=SIGNATURE from stripe-signature header
            parts: dict[str, str] = {}
            for item in sig_header.split(","):
                key, _, value = item.partition("=")
                parts[key.strip()] = value.strip()

            timestamp = parts["t"]
            expected_sig = parts["v1"]

            base_string = f"{timestamp}.{request_body.decode()}"
            computed = hmac.new(
                webhook_secret.encode(),
                base_string.encode(),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(computed, expected_sig)
        except Exception:
            return False
