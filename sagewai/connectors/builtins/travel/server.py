# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Travel MCP Server.

Exposes travel booking operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Amadeus / Booking.com API).

Run via stdio::

    python -m mcp_travel
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
SERVER_NAME = "travel"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory travel store
# ---------------------------------------------------------------------------


class _TravelStore:
    """Simple in-memory travel store for the MCP server."""

    def __init__(self) -> None:
        self.destinations: dict[str, dict[str, Any]] = {}
        self.flights: dict[str, dict[str, Any]] = {}
        self.hotels: dict[str, dict[str, Any]] = {}
        self.bookings: dict[str, dict[str, Any]] = {}

    def search_destinations(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [
            d
            for d in self.destinations.values()
            if q in d["name"].lower()
            or q in d.get("country", "").lower()
            or q in d.get("description", "").lower()
        ]

    def add_destination(
        self, name: str, country: str = "", description: str = ""
    ) -> dict[str, Any]:
        did = f"dest_{uuid4().hex[:12]}"
        dest = {
            "id": did,
            "name": name,
            "country": country,
            "description": description,
        }
        self.destinations[did] = dest
        return dest

    def search_flights(
        self, origin: str, destination: str, date: str
    ) -> list[dict[str, Any]]:
        return [
            f
            for f in self.flights.values()
            if f["origin"].lower() == origin.lower()
            and f["destination"].lower() == destination.lower()
            and f["date"] == date
        ]

    def add_flight(
        self,
        origin: str,
        destination: str,
        date: str,
        airline: str = "",
        price: int = 0,
        currency: str = "usd",
    ) -> dict[str, Any]:
        fid = f"flt_{uuid4().hex[:12]}"
        flight = {
            "id": fid,
            "origin": origin,
            "destination": destination,
            "date": date,
            "airline": airline,
            "price": price,
            "currency": currency.lower(),
            "available": True,
        }
        self.flights[fid] = flight
        return flight

    def search_hotels(
        self, location: str, check_in: str = "", check_out: str = ""
    ) -> list[dict[str, Any]]:
        loc = location.lower()
        results = [
            h for h in self.hotels.values() if loc in h["location"].lower()
        ]
        return results

    def add_hotel(
        self,
        name: str,
        location: str,
        price_per_night: int = 0,
        currency: str = "usd",
        rating: float = 0.0,
    ) -> dict[str, Any]:
        hid = f"htl_{uuid4().hex[:12]}"
        hotel = {
            "id": hid,
            "name": name,
            "location": location,
            "price_per_night": price_per_night,
            "currency": currency.lower(),
            "rating": rating,
            "available": True,
        }
        self.hotels[hid] = hotel
        return hotel

    def create_booking(
        self,
        booking_type: str,
        reference_id: str,
        traveler_name: str = "",
        traveler_email: str = "",
    ) -> dict[str, Any]:
        bid = f"bk_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        booking = {
            "id": bid,
            "type": booking_type,
            "reference_id": reference_id,
            "traveler_name": traveler_name,
            "traveler_email": traveler_email,
            "status": "confirmed",
            "created_at": now,
        }
        self.bookings[bid] = booking
        return booking

    def get_booking(self, booking_id: str) -> dict[str, Any] | None:
        return self.bookings.get(booking_id)

    def list_bookings(self, status: str = "") -> list[dict[str, Any]]:
        results = list(self.bookings.values())
        if status:
            results = [b for b in results if b["status"] == status]
        return results

    def cancel_booking(self, booking_id: str) -> dict[str, Any] | None:
        booking = self.bookings.get(booking_id)
        if not booking:
            return None
        booking["status"] = "cancelled"
        return booking


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _TravelStore:
    return _TravelStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "search_destinations",
        "description": "Search for travel destinations",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (city, country, etc.)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_destination",
        "description": "Add a destination to the catalog",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Destination name"},
                "country": {"type": "string", "description": "Country"},
                "description": {"type": "string", "description": "Description"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_flights",
        "description": "Search for available flights",
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin city/airport"},
                "destination": {"type": "string", "description": "Destination city/airport"},
                "date": {"type": "string", "description": "Travel date (YYYY-MM-DD)"},
            },
            "required": ["origin", "destination", "date"],
        },
    },
    {
        "name": "add_flight",
        "description": "Add a flight to the catalog",
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin"},
                "destination": {"type": "string", "description": "Destination"},
                "date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                "airline": {"type": "string", "description": "Airline name"},
                "price": {"type": "integer", "description": "Price in smallest currency unit"},
                "currency": {"type": "string", "description": "Currency (default: usd)"},
            },
            "required": ["origin", "destination", "date"],
        },
    },
    {
        "name": "search_hotels",
        "description": "Search for hotels at a location",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "Location to search"},
                "check_in": {"type": "string", "description": "Check-in date"},
                "check_out": {"type": "string", "description": "Check-out date"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "add_hotel",
        "description": "Add a hotel to the catalog",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hotel name"},
                "location": {"type": "string", "description": "Location"},
                "price_per_night": {"type": "integer", "description": "Price per night"},
                "currency": {"type": "string", "description": "Currency (default: usd)"},
                "rating": {"type": "number", "description": "Star rating (0-5)"},
            },
            "required": ["name", "location"],
        },
    },
    {
        "name": "create_booking",
        "description": "Create a travel booking (flight or hotel)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_type": {"type": "string", "description": "flight or hotel"},
                "reference_id": {"type": "string", "description": "Flight or hotel ID"},
                "traveler_name": {"type": "string", "description": "Traveler name"},
                "traveler_email": {"type": "string", "description": "Traveler email"},
            },
            "required": ["booking_type", "reference_id"],
        },
    },
    {
        "name": "get_booking",
        "description": "Get booking details by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Booking ID"},
            },
            "required": ["booking_id"],
        },
    },
    {
        "name": "list_bookings",
        "description": "List all bookings",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status"},
            },
        },
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a booking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Booking ID to cancel"},
            },
            "required": ["booking_id"],
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _TravelStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _TravelStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "search_destinations": lambda: store.search_destinations(args["query"]),
        "add_destination": lambda: store.add_destination(
            name=args["name"],
            country=args.get("country", ""),
            description=args.get("description", ""),
        ),
        "search_flights": lambda: store.search_flights(
            origin=args["origin"],
            destination=args["destination"],
            date=args["date"],
        ),
        "add_flight": lambda: store.add_flight(
            origin=args["origin"],
            destination=args["destination"],
            date=args["date"],
            airline=args.get("airline", ""),
            price=args.get("price", 0),
            currency=args.get("currency", "usd"),
        ),
        "search_hotels": lambda: store.search_hotels(
            location=args["location"],
            check_in=args.get("check_in", ""),
            check_out=args.get("check_out", ""),
        ),
        "add_hotel": lambda: store.add_hotel(
            name=args["name"],
            location=args["location"],
            price_per_night=args.get("price_per_night", 0),
            currency=args.get("currency", "usd"),
            rating=args.get("rating", 0.0),
        ),
        "create_booking": lambda: store.create_booking(
            booking_type=args["booking_type"],
            reference_id=args["reference_id"],
            traveler_name=args.get("traveler_name", ""),
            traveler_email=args.get("traveler_email", ""),
        ),
        "get_booking": lambda: store.get_booking(args["booking_id"]),
        "list_bookings": lambda: store.list_bookings(status=args.get("status", "")),
        "cancel_booking": lambda: store.cancel_booking(args["booking_id"]),
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

    logger.info("Travel MCP server listening on stdio")

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
