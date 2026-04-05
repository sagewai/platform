# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""A2A client — discover remote agents and delegate tasks via JSON-RPC.

Usage::

    from sagewai.protocols.a2a.client import A2AClient

    async with A2AClient() as client:
        card = await client.discover("https://agent.example.com")
        result = await client.send_task("https://agent.example.com", "Summarize this doc")
        status = await client.get_task("https://agent.example.com", result.id)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from sagewai.protocols.a2a.models import AgentCard
from sagewai.protocols.a2a.server import Task

logger = logging.getLogger(__name__)


class A2AError(Exception):
    """Error returned by a remote A2A server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"A2A error {code}: {message}")


class A2AClient:
    """Client for the A2A protocol — agent discovery and task delegation.

    Args:
        api_key: Optional Bearer token for authenticated endpoints.
        timeout: HTTP request timeout in seconds.
        base_headers: Extra headers to include in all requests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        base_headers: dict[str, str] | None = None,
    ) -> None:
        headers: dict[str, str] = {**(base_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def __aenter__(self) -> A2AClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover(self, base_url: str) -> AgentCard:
        """Fetch the agent card from a remote A2A server.

        Args:
            base_url: The agent's base URL (e.g. ``https://agent.example.com``).

        Returns:
            The parsed ``AgentCard``.
        """
        url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return AgentCard.model_validate(resp.json())

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    async def send_task(
        self,
        base_url: str,
        message: str,
        *,
        task_id: str | None = None,
    ) -> Task:
        """Send a task to a remote agent via tasks/send.

        Args:
            base_url: The agent's base URL.
            message: The task message to send.
            task_id: Optional task ID. Server generates one if omitted.

        Returns:
            The completed ``Task`` with status and artifacts.
        """
        params: dict[str, Any] = {"message": message}
        if task_id:
            params["id"] = task_id
        result = await self._jsonrpc(base_url, "tasks/send", params)
        return Task.model_validate(result)

    async def get_task(self, base_url: str, task_id: str) -> Task:
        """Get the status of a task via tasks/get.

        Args:
            base_url: The agent's base URL.
            task_id: The task ID to query.

        Returns:
            The ``Task`` with current status.
        """
        result = await self._jsonrpc(base_url, "tasks/get", {"id": task_id})
        return Task.model_validate(result)

    async def cancel_task(self, base_url: str, task_id: str) -> Task:
        """Cancel a task via tasks/cancel.

        Args:
            base_url: The agent's base URL.
            task_id: The task ID to cancel.

        Returns:
            The ``Task`` with updated (canceled) status.
        """
        result = await self._jsonrpc(base_url, "tasks/cancel", {"id": task_id})
        return Task.model_validate(result)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _jsonrpc(
        self,
        base_url: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request to the A2A endpoint."""
        url = f"{base_url.rstrip('/')}/a2a"
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": method,
            "params": params,
        }
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            err = data["error"]
            raise A2AError(
                code=err.get("code", -1),
                message=err.get("message", "Unknown error"),
                data=err.get("data"),
            )

        return data["result"]
