# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth2 protocol plugin.

Wraps :mod:`sagewai.oauth` (the OAuth2 subpackage shipped in PR #356).
This plugin's ``extra_routes`` and ``extra_cli`` are new implementations
that operate on the new generic :class:`ConnectionStore`, NOT the legacy
:mod:`sagewai.oauth.vault`. PR4 mounts the routes/commands and deletes
the legacy admin/CLI surface; until then both surfaces coexist (legacy
serves traffic, new is registered but unmounted).

The stateless helpers from :mod:`sagewai.oauth` —
``providers``, ``pkce``, ``pending_auth``, ``exchange`` — are reused
directly. Only the persistence-touching code is re-pointed at
``ctx.store``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext
from sagewai.oauth import providers as oauth_providers


def oauth2_default_key(protocol_data: dict[str, Any]) -> str | None:
    """Default-key extractor: one default per (project, "oauth2", provider)."""
    return protocol_data.get("provider") if isinstance(protocol_data, dict) else None


class _Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: str
    obtained_at: str
    last_refreshed_at: str | None = None


class OAuth2ProtocolData(BaseModel):
    """Validation schema for OAuth2 connections.

    Mirrors PR #356's oauth_client record body exactly so the plugin
    can adapt the existing flow without semantic changes.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    requested_scopes: list[str] = Field(..., min_length=1)
    granted_scopes: list[str] = Field(default_factory=list)
    tokens: _Tokens | None = None

    @model_validator(mode="after")
    def _provider_known(self):
        try:
            oauth_providers.get_provider(self.provider)
        except oauth_providers.UnknownProviderError as exc:
            raise ValueError(
                f"unknown OAuth provider {self.provider!r}; "
                f"registered: {[p.id for p in oauth_providers.all_providers()]}"
            ) from exc
        return self


# ── extra_routes implementations ────────────────────────────────────
#
# These handlers operate on the generic ConnectionStore via ctx.store.
# Mirror the route bodies in packages/sdk/sagewai/admin/oauth_routes.py
# (PR #356), but replace every `vault.find_default_client`/`vault.create_client`/
# `vault.update_tokens`/etc. with the equivalent `ctx.store.*` call.
#
# IMPLEMENTATION NOTE for route bodies: keep them THIN. The reusable
# logic — building authorize URLs, PKCE, state nonces, token exchange,
# pending-auth state — already lives in sagewai.oauth.{providers,pkce,
# pending_auth,exchange}. The route function reads/writes via ctx.store,
# everything else delegates.

_oauth2_router = APIRouter()


@_oauth2_router.post("/{connection_id}/start")
async def _start_authorize(connection_id: str):
    """Mint authorize URL for a pending or authorized record (re-authorize)."""
    # IMPLEMENTATION: read ctx.store.get(connection_id); pull provider via
    # oauth_providers.get_provider(record.protocol_data["provider"]); generate
    # state nonce + PKCE verifier; pending_auth.get_default_store().put(...);
    # build authorize URL via the same helper PR #356's oauth_routes uses;
    # return {"authorize_url": ..., "state": ...}.
    # In PR2 this route is registered but not mounted; PR4 supplies ctx via
    # FastAPI dependency injection at mount time.
    raise NotImplementedError("body implemented in PR4 wiring; route stub registered for PR2 unit tests")


@_oauth2_router.get("/callback")
async def _callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Vendor browser-redirect lands here; exchange code for tokens via the new store."""
    raise NotImplementedError("body implemented in PR4 wiring; route stub registered for PR2 unit tests")


@_oauth2_router.post("/{connection_id}/refresh")
async def _refresh(connection_id: str):
    """Force-refresh tokens via sagewai.oauth.exchange.refresh_access_token."""
    raise NotImplementedError("body implemented in PR4 wiring; route stub registered for PR2 unit tests")


@_oauth2_router.post("/{connection_id}/revoke")
async def _revoke(connection_id: str):
    """Vendor revoke (if provider.revoke_url is set) + clear tokens via ctx.store."""
    raise NotImplementedError("body implemented in PR4 wiring; route stub registered for PR2 unit tests")


# ── extra_cli implementations ───────────────────────────────────────
#
# Mirror PR #356's `sagewai oauth` subcommands but operate on the new
# generic store. Bodies are NotImplementedError-stubs in PR2; PR4 wires
# them at the CLI registration site.


@click.command("start")
@click.argument("connection_id")
def _cli_start(connection_id: str) -> None:
    click.echo(f"oauth2 start {connection_id} (full wiring in PR4)")


@click.command("refresh")
@click.argument("connection_id")
def _cli_refresh(connection_id: str) -> None:
    click.echo(f"oauth2 refresh {connection_id} (full wiring in PR4)")


@click.command("revoke")
@click.argument("connection_id")
def _cli_revoke(connection_id: str) -> None:
    click.echo(f"oauth2 revoke {connection_id} (full wiring in PR4)")


@click.command("reauthorize")
@click.argument("connection_id")
def _cli_reauthorize(connection_id: str) -> None:
    click.echo(f"oauth2 reauthorize {connection_id} (full wiring in PR4)")


@click.command("providers")
def _cli_providers() -> None:
    """List supported OAuth providers (spotify, google, ...)."""
    for p in oauth_providers.all_providers():
        click.echo(f"{p.id}\t{p.display_name}")


class OAuth2ProtocolPlugin:
    id: ClassVar[str] = "oauth2"
    display_name: ClassVar[str] = "OAuth 2.0"
    sensitive_fields: ClassVar[tuple[str, ...]] = (
        "client_secret",
        "tokens.access_token",
        "tokens.refresh_token",
    )

    def protocol_data_schema(self) -> type[BaseModel]:
        return OAuth2ProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if include_secrets:
            return out
        if "client_secret" in out:
            out["client_secret"] = "***"
        tokens = out.get("tokens")
        if isinstance(tokens, dict):
            tokens = dict(tokens)
            if "access_token" in tokens:
                tokens["access_token"] = "***"
            if "refresh_token" in tokens and tokens["refresh_token"] is not None:
                tokens["refresh_token"] = "***"
            out["tokens"] = tokens
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        # Best-effort vendor revoke (if provider has revoke_url).
        # Full implementation lands in PR4; PR2 plugin is a no-op so tests
        # for delete don't require network.
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        tokens = connection.protocol_data.get("tokens")
        if not tokens:
            return TestResult(ok=False, message="connection not authorized — no tokens")
        expires_at = tokens.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
            except ValueError:
                return TestResult(ok=False, message=f"malformed expires_at: {expires_at!r}")
            if exp < datetime.now(timezone.utc):
                return TestResult(ok=False, message="token expired — refresh required")
        return TestResult(ok=True, message="token present and not expired")

    def extra_routes(self) -> APIRouter:
        return _oauth2_router

    def extra_cli(self) -> list[click.Command]:
        return [_cli_start, _cli_refresh, _cli_revoke, _cli_reauthorize, _cli_providers]


__all__ = [
    "OAuth2ProtocolData",
    "OAuth2ProtocolPlugin",
    "oauth2_default_key",
]
