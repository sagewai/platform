# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Travel MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

TRAVEL_CONTRACT = ServerContract(
    server_name="travel",
    tool_count=10,
    tools={
        "search_destinations": ToolContract(
            name="search_destinations",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "add_destination": ToolContract(
            name="add_destination",
            required_params=["name"],
            optional_params=["country", "description"],
            param_types={"name": "string", "country": "string", "description": "string"},
            response_fields=["name"],
        ),
        "search_flights": ToolContract(
            name="search_flights",
            required_params=["origin", "destination", "date"],
            param_types={"origin": "string", "destination": "string", "date": "string"},
        ),
        "add_flight": ToolContract(
            name="add_flight",
            required_params=["origin", "destination", "date"],
            optional_params=["airline", "price", "currency"],
            param_types={
                "origin": "string",
                "destination": "string",
                "date": "string",
                "airline": "string",
                "price": "integer",
                "currency": "string",
            },
        ),
        "search_hotels": ToolContract(
            name="search_hotels",
            required_params=["location"],
            optional_params=["check_in", "check_out"],
            param_types={"location": "string", "check_in": "string", "check_out": "string"},
        ),
        "add_hotel": ToolContract(
            name="add_hotel",
            required_params=["name", "location"],
            optional_params=["price_per_night", "currency", "rating"],
            param_types={
                "name": "string",
                "location": "string",
                "price_per_night": "integer",
                "currency": "string",
                "rating": "number",
            },
        ),
        "create_booking": ToolContract(
            name="create_booking",
            required_params=["booking_type", "reference_id"],
            optional_params=["traveler_name", "traveler_email"],
            param_types={
                "booking_type": "string",
                "reference_id": "string",
                "traveler_name": "string",
                "traveler_email": "string",
            },
            response_fields=["booking_id", "booking_type", "status"],
        ),
        "get_booking": ToolContract(
            name="get_booking",
            required_params=["booking_id"],
            param_types={"booking_id": "string"},
            response_fields=["booking_id", "booking_type", "status"],
        ),
        "list_bookings": ToolContract(
            name="list_bookings",
            optional_params=["status"],
            param_types={"status": "string"},
        ),
        "cancel_booking": ToolContract(
            name="cancel_booking",
            required_params=["booking_id"],
            param_types={"booking_id": "string"},
        ),
    },
)
