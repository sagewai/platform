# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Travel connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class TravelConnector(ConnectorSpec):
    """Amadeus travel connector."""

    name: str = "travel"
    display_name: str = "Amadeus"
    category: str = "travel"
    description: str = (
        "Search flights, hotels, and manage travel bookings via Amadeus."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="client_id",
            label="Client ID",
            env_var="AMADEUS_CLIENT_ID",
            secret=False,
        ),
        AuthField(
            key="client_secret",
            label="Client Secret",
            env_var="AMADEUS_CLIENT_SECRET",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.travel.server",
    ]
    docs_url: str = "https://developers.amadeus.com"
