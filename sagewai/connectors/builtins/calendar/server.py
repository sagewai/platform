# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Calendar MCP Server.

Exposes calendar operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Google Calendar API).

Run via stdio::

    python -m mcp_calendar
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "calendar"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory calendar store
# ---------------------------------------------------------------------------


class _CalendarStore:
    """Simple in-memory calendar store for the MCP server."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.calendars: dict[str, dict[str, Any]] = {}

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        calendar_id: str | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        eid = f"evt_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "id": eid,
            "title": title,
            "start": start,
            "end": end,
            "calendar_id": calendar_id,
            "description": description,
            "location": location,
            "attendees": attendees or [],
            "status": "confirmed",
            "created_at": now,
        }
        self.events[eid] = event
        return event

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return self.events.get(event_id)

    def update_event(self, event_id: str, **kwargs: Any) -> dict[str, Any] | None:
        event = self.events.get(event_id)
        if not event:
            return None
        for key in ("title", "start", "end", "description", "location", "attendees", "status"):
            if key in kwargs and kwargs[key] is not None:
                event[key] = kwargs[key]
        return event

    def delete_event(self, event_id: str) -> bool:
        return self.events.pop(event_id, None) is not None

    def list_events(
        self, calendar_id: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        results = list(self.events.values())
        if calendar_id:
            results = [e for e in results if e["calendar_id"] == calendar_id]
        if status:
            results = [e for e in results if e["status"] == status]
        return results

    def search_events(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [
            e
            for e in self.events.values()
            if q in e["title"].lower()
            or q in e.get("description", "").lower()
            or q in e.get("location", "").lower()
        ]

    def check_availability(self, start: str, end: str) -> dict[str, Any]:
        conflicts = [
            e
            for e in self.events.values()
            if e["status"] == "confirmed" and e["start"] < end and e["end"] > start
        ]
        return {"available": len(conflicts) == 0, "conflicts": conflicts}

    def create_calendar(self, name: str, description: str = "") -> dict[str, Any]:
        cid = f"cal_{uuid4().hex[:12]}"
        cal = {
            "id": cid,
            "name": name,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.calendars[cid] = cal
        return cal

    def list_calendars(self) -> list[dict[str, Any]]:
        return list(self.calendars.values())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _CalendarStore:
    return _CalendarStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_event",
        "description": "Create a new calendar event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time (ISO 8601)"},
                "end": {"type": "string", "description": "End time (ISO 8601)"},
                "calendar_id": {"type": "string", "description": "Calendar ID"},
                "description": {"type": "string", "description": "Event description"},
                "location": {"type": "string", "description": "Event location"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee emails",
                },
            },
            "required": ["title", "start", "end"],
        },
    },
    {
        "name": "get_event",
        "description": "Get a calendar event by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "update_event",
        "description": "Update an existing calendar event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID"},
                "title": {"type": "string", "description": "New title"},
                "start": {"type": "string", "description": "New start time"},
                "end": {"type": "string", "description": "New end time"},
                "description": {"type": "string", "description": "New description"},
                "location": {"type": "string", "description": "New location"},
                "status": {"type": "string", "description": "confirmed or cancelled"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete a calendar event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "list_events",
        "description": "List calendar events with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Filter by calendar"},
                "status": {"type": "string", "description": "Filter by status"},
            },
        },
    },
    {
        "name": "search_events",
        "description": "Search events by title, description, or location",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_availability",
        "description": "Check if a time slot is available",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start time (ISO 8601)"},
                "end": {"type": "string", "description": "End time (ISO 8601)"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "create_calendar",
        "description": "Create a new calendar",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Calendar name"},
                "description": {"type": "string", "description": "Calendar description"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_calendars",
        "description": "List all calendars",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def handle_request(
    raw: dict[str, Any], store: _CalendarStore | None = None
) -> dict[str, Any]:
    """Process a single MCP JSON-RPC request."""
    s = store or _store
    request_id = raw.get("id")
    method = raw.get("method", "")
    params = raw.get("params", {})

    if method == "initialize":
        return _jsonrpc_response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "ping":
        return _jsonrpc_response(request_id, {})

    if method == "tools/list":
        return _jsonrpc_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        return await _handle_tool_call(request_id, params, s)

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


async def _handle_tool_call(
    request_id: Any, params: dict[str, Any], store: _CalendarStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_event": lambda: store.create_event(
            title=args["title"],
            start=args["start"],
            end=args["end"],
            calendar_id=args.get("calendar_id"),
            description=args.get("description", ""),
            location=args.get("location", ""),
            attendees=args.get("attendees"),
        ),
        "get_event": lambda: store.get_event(args["event_id"]),
        "update_event": lambda: store.update_event(
            event_id=args["event_id"],
            title=args.get("title"),
            start=args.get("start"),
            end=args.get("end"),
            description=args.get("description"),
            location=args.get("location"),
            status=args.get("status"),
        ),
        "delete_event": lambda: store.delete_event(args["event_id"]),
        "list_events": lambda: store.list_events(
            calendar_id=args.get("calendar_id", ""),
            status=args.get("status", ""),
        ),
        "search_events": lambda: store.search_events(args["query"]),
        "check_availability": lambda: store.check_availability(
            start=args["start"], end=args["end"]
        ),
        "create_calendar": lambda: store.create_calendar(
            name=args["name"], description=args.get("description", "")
        ),
        "list_calendars": lambda: store.list_calendars(),
    }

    handler = handlers.get(tool_name)
    if not handler:
        return _jsonrpc_error(request_id, -32602, f"Unknown tool: {tool_name}")

    try:
        result = handler()
        content = json.dumps(result, default=str)
        return _jsonrpc_response(
            request_id, {"content": [{"type": "text", "text": content}]}
        )
    except Exception as exc:
        logger.exception("Tool call error: %s", tool_name)
        return _jsonrpc_error(request_id, -32000, str(exc))


async def run_stdio() -> None:
    """Run the MCP server over stdin/stdout."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = (
        await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
    )
    writer = asyncio.StreamWriter(
        writer_transport, writer_protocol, None, asyncio.get_event_loop()
    )

    logger.info("Calendar MCP server listening on stdio")

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = await handle_request(raw)
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()


if __name__ == "__main__":
    asyncio.run(run_stdio())
