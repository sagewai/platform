# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Slack MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

SLACK_CONTRACT = ServerContract(
    server_name="slack",
    tool_count=11,
    tools={
        "create_channel": ToolContract(
            name="create_channel",
            required_params=["name"],
            optional_params=["topic", "is_private"],
            param_types={"name": "string", "topic": "string", "is_private": "boolean"},
            response_fields=["channel_id", "name"],
        ),
        "get_channel": ToolContract(
            name="get_channel",
            required_params=["channel_id"],
            param_types={"channel_id": "string"},
            response_fields=["channel_id", "name"],
        ),
        "list_channels": ToolContract(
            name="list_channels",
        ),
        "send_message": ToolContract(
            name="send_message",
            required_params=["channel_id", "text"],
            optional_params=["user_id", "thread_ts"],
            param_types={
                "channel_id": "string",
                "text": "string",
                "user_id": "string",
                "thread_ts": "string",
            },
            response_fields=["message_id", "channel_id", "text"],
        ),
        "get_message": ToolContract(
            name="get_message",
            required_params=["message_id"],
            param_types={"message_id": "string"},
            response_fields=["message_id", "channel_id", "text"],
        ),
        "list_messages": ToolContract(
            name="list_messages",
            optional_params=["channel_id"],
            param_types={"channel_id": "string"},
        ),
        "search_messages": ToolContract(
            name="search_messages",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "delete_message": ToolContract(
            name="delete_message",
            required_params=["message_id"],
            param_types={"message_id": "string"},
        ),
        "add_user": ToolContract(
            name="add_user",
            required_params=["name"],
            optional_params=["email"],
            param_types={"name": "string", "email": "string"},
            response_fields=["user_id", "name"],
        ),
        "get_user": ToolContract(
            name="get_user",
            required_params=["user_id"],
            param_types={"user_id": "string"},
            response_fields=["user_id", "name"],
        ),
        "list_users": ToolContract(
            name="list_users",
        ),
    },
)
