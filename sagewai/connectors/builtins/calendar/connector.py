# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Calendar connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class CalendarConnector(ConnectorSpec):
    """Google Calendar connector."""

    name: str = "calendar"
    display_name: str = "Google Calendar"
    category: str = "productivity"
    description: str = "Create, update, and manage Google Calendar events."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="credentials_json",
            label="Credentials JSON",
            env_var="GOOGLE_CALENDAR_CREDENTIALS",
            hint="Service account JSON",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.calendar.server",
    ]
    docs_url: str = "https://developers.google.com/calendar"
    supports_poller: bool = True
