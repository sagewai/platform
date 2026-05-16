# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WhatsApp Business API connector."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class WhatsAppConnector(ConnectorSpec):
    """WhatsApp Business API connector for messaging."""

    name: str = "whatsapp"
    display_name: str = "WhatsApp"
    category: str = "communication"
    description: str = "Send and receive WhatsApp messages via the Business API."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="access_token",
            label="Access Token",
            env_var="WHATSAPP_ACCESS_TOKEN",
        ),
        AuthField(
            key="phone_number_id",
            label="Phone Number ID",
            env_var="WHATSAPP_PHONE_NUMBER_ID",
            secret=False,
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.whatsapp.server",
    ]
    docs_url: str = "https://developers.facebook.com/docs/whatsapp"
    supports_webhook: bool = True
    supports_poller: bool = True
