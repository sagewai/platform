# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Email MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

EMAIL_CONTRACT = ServerContract(
    server_name="email",
    tool_count=8,
    tools={
        "send_email": ToolContract(
            name="send_email",
            required_params=["to", "subject", "body"],
            optional_params=["cc", "bcc"],
            param_types={
                "to": "array",
                "subject": "string",
                "body": "string",
                "cc": "array",
                "bcc": "array",
            },
            response_fields=["email_id", "to", "subject", "status"],
        ),
        "get_email": ToolContract(
            name="get_email",
            required_params=["email_id"],
            param_types={"email_id": "string"},
            response_fields=["email_id", "to", "subject", "body"],
        ),
        "list_emails": ToolContract(
            name="list_emails",
            optional_params=["folder", "status"],
            param_types={"folder": "string", "status": "string"},
        ),
        "search_emails": ToolContract(
            name="search_emails",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "delete_email": ToolContract(
            name="delete_email",
            required_params=["email_id"],
            param_types={"email_id": "string"},
        ),
        "create_draft": ToolContract(
            name="create_draft",
            required_params=["to", "subject", "body"],
            param_types={"to": "array", "subject": "string", "body": "string"},
            response_fields=["email_id", "to", "subject", "status"],
        ),
        "list_drafts": ToolContract(
            name="list_drafts",
        ),
        "reply_to_email": ToolContract(
            name="reply_to_email",
            required_params=["email_id", "body"],
            param_types={"email_id": "string", "body": "string"},
        ),
    },
)
