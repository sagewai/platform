# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Documents connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class DocumentsConnector(ConnectorSpec):
    """Google Drive documents connector."""

    name: str = "documents"
    display_name: str = "Google Drive"
    category: str = "productivity"
    description: str = (
        "Upload, download, search, and manage Google Drive documents."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="credentials_json",
            label="Credentials JSON",
            env_var="GOOGLE_DRIVE_CREDENTIALS",
            hint="Service account JSON",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.documents.server",
    ]
    docs_url: str = "https://developers.google.com/drive"
