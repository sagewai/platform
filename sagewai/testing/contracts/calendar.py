# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Calendar MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

CALENDAR_CONTRACT = ServerContract(
    server_name="calendar",
    tool_count=9,
    tools={
        "create_event": ToolContract(
            name="create_event",
            required_params=["title", "start", "end"],
            optional_params=["calendar_id", "description", "location", "attendees"],
            param_types={
                "title": "string",
                "start": "string",
                "end": "string",
                "calendar_id": "string",
                "description": "string",
                "location": "string",
                "attendees": "array",
            },
            response_fields=["event_id", "title", "start", "end"],
        ),
        "get_event": ToolContract(
            name="get_event",
            required_params=["event_id"],
            param_types={"event_id": "string"},
            response_fields=["event_id", "title", "start", "end"],
        ),
        "update_event": ToolContract(
            name="update_event",
            required_params=["event_id"],
            optional_params=["title", "start", "end", "description", "location", "status"],
            param_types={
                "event_id": "string",
                "title": "string",
                "start": "string",
                "end": "string",
                "description": "string",
                "location": "string",
                "status": "string",
            },
        ),
        "delete_event": ToolContract(
            name="delete_event",
            required_params=["event_id"],
            param_types={"event_id": "string"},
        ),
        "list_events": ToolContract(
            name="list_events",
            optional_params=["calendar_id", "status"],
            param_types={"calendar_id": "string", "status": "string"},
        ),
        "search_events": ToolContract(
            name="search_events",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "check_availability": ToolContract(
            name="check_availability",
            required_params=["start", "end"],
            param_types={"start": "string", "end": "string"},
        ),
        "create_calendar": ToolContract(
            name="create_calendar",
            required_params=["name"],
            optional_params=["description"],
            param_types={"name": "string", "description": "string"},
            response_fields=["calendar_id", "name"],
        ),
        "list_calendars": ToolContract(
            name="list_calendars",
        ),
    },
)
