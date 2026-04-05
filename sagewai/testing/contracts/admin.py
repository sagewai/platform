# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Admin MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

ADMIN_CONTRACT = ServerContract(
    server_name="admin",
    tool_count=10,
    tools={
        "list_agents": ToolContract(
            name="list_agents",
        ),
        "get_agent": ToolContract(
            name="get_agent",
            required_params=["agent_name"],
            param_types={"agent_name": "string"},
        ),
        "list_runs": ToolContract(
            name="list_runs",
            optional_params=["agent_name", "status", "limit", "offset"],
            param_types={
                "agent_name": "string",
                "status": "string",
                "limit": "integer",
                "offset": "integer",
            },
        ),
        "get_run": ToolContract(
            name="get_run",
            required_params=["run_id"],
            param_types={"run_id": "string"},
        ),
        "list_sessions": ToolContract(
            name="list_sessions",
        ),
        "get_session": ToolContract(
            name="get_session",
            required_params=["session_id"],
            param_types={"session_id": "string"},
        ),
        "pause_run": ToolContract(
            name="pause_run",
            required_params=["run_id"],
            param_types={"run_id": "string"},
        ),
        "resume_run": ToolContract(
            name="resume_run",
            required_params=["run_id"],
            param_types={"run_id": "string"},
        ),
        "cancel_run": ToolContract(
            name="cancel_run",
            required_params=["run_id"],
            param_types={"run_id": "string"},
        ),
        "update_agent_config": ToolContract(
            name="update_agent_config",
            required_params=["agent_name"],
            optional_params=["model", "system_prompt", "temperature", "max_tokens", "max_iterations"],
            param_types={
                "agent_name": "string",
                "model": "string",
                "system_prompt": "string",
                "temperature": "number",
                "max_tokens": "integer",
                "max_iterations": "integer",
            },
        ),
    },
)
