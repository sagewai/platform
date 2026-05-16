# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Webhook router — receives and verifies incoming webhooks from connectors."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request, Response

from sagewai.connectors.base import ConnectorSpec

logger = logging.getLogger(__name__)


class WebhookRouter:
    """Registers webhook endpoints that route incoming events to handlers."""

    def __init__(self) -> None:
        self._router = APIRouter(prefix="/webhooks")
        self._registrations: list[dict] = []

    def register_connector_webhook(
        self,
        path: str,
        connector: ConnectorSpec,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
        credentials: dict[str, str],
    ) -> None:
        """Register a webhook endpoint for a connector."""
        self._registrations.append({
            "path": path,
            "connector": connector,
            "handler": handler,
            "credentials": credentials,
        })

        async def _endpoint(request: Request) -> Response:
            body = await request.body()
            headers = dict(request.headers)

            # Verify webhook signature
            verified = await connector.verify_webhook(body, headers, credentials)
            if not verified:
                return Response(status_code=401, content="Webhook verification failed")

            # Parse and dispatch
            try:
                payload = json.loads(body)
            except Exception:
                payload = {"raw": body.decode("utf-8", errors="replace")}

            await handler(payload)
            return Response(status_code=200, content="OK")

        self._router.add_api_route(
            f"/{path}",
            _endpoint,
            methods=["POST"],
        )

    def get_fastapi_router(self) -> APIRouter:
        """Return the FastAPI router with all registered webhook endpoints."""
        return self._router
