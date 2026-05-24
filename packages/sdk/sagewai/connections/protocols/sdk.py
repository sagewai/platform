# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SDK builtin plugin.

For SDK-builtin tools (``packages/sdk/sagewai/tools/builtins/*``) that
need credentials but no transport — PayPal today, others later.
Class-level ``sensitive_fields`` is empty; per-record masking derives
sensitive credential names from each connection's ``credential_fields``
list.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext


class _CredentialField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    type: Literal["password", "text"]
    description: str | None = None


class SdkProtocolData(BaseModel):
    """Validation schema for SDK builtins."""

    model_config = ConfigDict(extra="allow")  # `secrets` dict added at write time

    entrypoint: str = Field(..., pattern=r"^[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+$")
    credential_fields: list[_CredentialField] = Field(default_factory=list)


class SdkProtocolPlugin:
    """SDK builtin plugin."""

    id: ClassVar[str] = "sdk"
    display_name: ClassVar[str] = "SDK builtin"
    sensitive_fields: ClassVar[tuple[str, ...]] = ()

    def protocol_data_schema(self) -> type[BaseModel]:
        return SdkProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if include_secrets:
            return out
        # Mask any secrets whose name matches a credential_fields entry with type "password".
        password_fields = {
            f["name"] for f in out.get("credential_fields", [])
            if isinstance(f, dict) and f.get("type") == "password"
        }
        secrets = out.get("secrets")
        if isinstance(secrets, dict) and password_fields:
            out["secrets"] = {
                k: ("***" if k in password_fields else v) for k, v in secrets.items()
            }
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        # SDK builtins have no transport to probe; credential validity surfaces on first call.
        return TestResult(ok=True, message="sdk plugin has no live test")

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = ["SdkProtocolPlugin", "SdkProtocolData"]
