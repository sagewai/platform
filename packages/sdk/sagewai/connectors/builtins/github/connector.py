# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""GitHub connector spec."""

import hashlib
import hmac

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class GitHubConnector(ConnectorSpec):
    """GitHub repository and issue management connector."""

    name: str = "github"
    display_name: str = "GitHub"
    category: str = "productivity"
    description: str = (
        "Manage repositories, issues, pull requests, and code reviews on GitHub."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="token",
            label="Personal Access Token",
            env_var="GITHUB_TOKEN",
            hint="From github.com/settings/tokens",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.github.server",
    ]
    docs_url: str = "https://docs.github.com/en/rest"
    agent_description: str = (
        "Interact with GitHub to create issues, review pull requests, "
        "search repositories, and manage project boards."
    )
    example_prompt: str = "List all open issues labeled 'bug' in the sagewai repo."
    supports_webhook: bool = True
    supports_poller: bool = True

    async def verify_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
        credentials: dict[str, str],
    ) -> bool:
        """Verify GitHub webhook signature using HMAC-SHA256."""
        try:
            secret = credentials.get("webhook_secret", "")
            signature = headers.get("x-hub-signature-256", "")
            if not secret or not signature:
                return False

            computed = (
                "sha256="
                + hmac.new(
                    secret.encode(),
                    request_body,
                    hashlib.sha256,
                ).hexdigest()
            )
            return hmac.compare_digest(computed, signature)
        except Exception:
            return False
