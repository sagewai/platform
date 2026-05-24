# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HTTP / REST plugin.

Wraps the existing http catalog-entry shape. Validates ``protocol_data``
against the same fields the http executor consumes. ``sensitive_fields``
is empty — secrets for catalogued tools live on api_key tier connection
records (PR3 wires per-field encryption).
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

import click
import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext


class _HttpAuth(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["none", "api_key", "bearer", "basic", "oauth2", "hmac", "aws_sigv4"]


class _HttpOperation(BaseModel):
    model_config = ConfigDict(extra="allow")

    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"]
    path: str


class HttpProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of HTTP connections."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(..., pattern=r"^https?://")
    auth: _HttpAuth
    runtime_base_url_field: str | None = None
    operations_ref: str | None = None
    operations: dict[str, _HttpOperation] | None = None


class HttpProtocolPlugin:
    """HTTP / REST plugin — catalogued and ad-hoc REST connections."""

    id: ClassVar[str] = "http"
    display_name: ClassVar[str] = "HTTP / REST"
    sensitive_fields: ClassVar[tuple[str, ...]] = ()

    def protocol_data_schema(self) -> type[BaseModel]:
        return HttpProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        # No sensitive fields — return as-is.
        return dict(protocol_data)

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        base_url = connection.protocol_data.get("base_url")
        if not base_url:
            return TestResult(ok=False, message="missing base_url")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.head(base_url)
        except httpx.HTTPError as exc:
            return TestResult(ok=False, message=str(exc))
        ok = 200 <= resp.status_code < 400
        return TestResult(ok=ok, status_code=resp.status_code)

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = ["HttpProtocolPlugin", "HttpProtocolData"]
