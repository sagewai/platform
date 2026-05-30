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
The plugin's ``extra_routes`` + ``extra_cli`` operate on the generic
:class:`ConnectionStore` and :class:`CredentialsBackendRouter` — no
references to the deleted legacy :mod:`sagewai.oauth.vault`.

The stateless helpers from :mod:`sagewai.oauth` — ``providers``,
``pkce``, ``pending_auth``, ``exchange`` — are reused directly. Only
the persistence-touching code is re-pointed at ``ctx.store`` +
``ctx.router``.
"""
from __future__ import annotations

import json as _json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import click
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext
from sagewai.oauth import exchange, pending_auth, pkce
from sagewai.oauth import providers as oauth_providers
from sagewai.oauth.errors import (
    OAuthCallbackError,
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
)


logger = logging.getLogger(__name__)


def oauth2_default_key(protocol_data: dict[str, Any]) -> str | None:
    """Default-key extractor: one default per (project, "oauth2", provider)."""
    return protocol_data.get("provider") if isinstance(protocol_data, dict) else None


def _emit_refresh_event(
    *,
    success: bool,
    connection_id: str,
    project_id: str | None,
    provider: str | None,
    trigger: str,
    old_expires_at: str | None = None,
    new_expires_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Emit a structured audit event for an OAuth2 token refresh.

    Uses the canonical ``logger.info(extra=...)`` pattern (PR #368) so the
    OTel pipeline ships the event to whatever audit sink is wired. A failed
    refresh is logged at INFO level (not ERROR) — the caller still receives
    the typed exception; the audit event is informational.

    ``trigger`` is a free-form string (``api_call`` | ``test`` | ``explicit``
    canonically) so callers can label the call context.
    """
    if success:
        logger.info(
            "oauth2 token refreshed",
            extra={
                "event": "oauth2.token.refreshed",
                "connection_id": connection_id,
                "project_id": project_id,
                "provider": provider,
                "trigger": trigger,
                "old_expires_at": old_expires_at,
                "new_expires_at": new_expires_at,
            },
        )
    else:
        logger.info(
            "oauth2 token refresh failed",
            extra={
                "event": "oauth2.token.refresh_failed",
                "connection_id": connection_id,
                "project_id": project_id,
                "provider": provider,
                "trigger": trigger,
                "error_code": error_code,
                "error_message": error_message,
            },
        )


class _Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: str
    obtained_at: str
    last_refreshed_at: str | None = None
    refresh_count: int = Field(default=0, ge=0)


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


# ── Context injection (test + production) ──────────────────────────
#
# Route handlers + CLI commands need access to the ConnectionsContext at
# request time. We use a module-level injectable singleton: PR4 admin
# wiring sets it once at app construction; CLI commands set it
# per-invocation; tests use ``_test_inject_context``.
#
# This is the simplest path that keeps route handlers as plain async
# functions; FastAPI ``Depends`` would require the registering site to
# wire the dependency.

_INJECTED_CTX = None


def _test_inject_context(ctx) -> None:
    """Test/CLI hook: set the context the route bodies will use.

    Pass ``None`` to clear.
    """
    global _INJECTED_CTX
    _INJECTED_CTX = ctx


def _get_ctx():
    """Return the active :class:`ConnectionsContext`.

    Falls back to constructing a fresh context from the
    :class:`AdminStateFile` at the platform-default path when nothing
    has been injected (production path).
    """
    if _INJECTED_CTX is not None:
        return _INJECTED_CTX
    # Production path: construct fresh from AdminStateFile.
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
    from sagewai.connections.bootstrap import build_connections_context

    return build_connections_context(AdminStateFile(default_admin_state_path()))


# ── HTML helper for /callback responses ─────────────────────────────


def _html_page(message: str, status_code: int = 200) -> HTMLResponse:
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Sagewai OAuth</title>"
        "<style>body{font-family:system-ui,sans-serif;display:flex;"
        "align-items:center;justify-content:center;min-height:80vh;"
        "background:#fafafa;color:#222;margin:0;padding:1rem;}"
        "main{max-width:32rem;text-align:center;}"
        "p{font-size:1.05rem;line-height:1.5;}</style></head>"
        f"<body><main><p>{message}</p></main></body></html>"
    )
    return HTMLResponse(content=body, status_code=status_code)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_authorize_url(
    provider: "oauth_providers.OAuthProvider",
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    """Vendor authorize URL with PKCE and CSRF params."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": provider.scope_separator.join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if provider.id == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"


# ── extra_routes implementations ────────────────────────────────────

_oauth2_router = APIRouter()


@_oauth2_router.post("/{connection_id}/start")
async def _start_authorize(connection_id: str) -> dict:
    """Mint authorize URL + stash a pending-auth entry."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "oauth2":
        raise HTTPException(
            400, f"connection {connection_id} is not oauth2 (got {record.protocol})"
        )
    # Decrypt to read client_id (plaintext) + provider; client_id is not
    # actually sensitive but lives alongside the secrets — decrypting is
    # idempotent against already-plaintext fields.
    decrypted_pd = ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    try:
        provider = oauth_providers.get_provider(decrypted_pd["provider"])
    except oauth_providers.UnknownProviderError as exc:
        raise HTTPException(500, f"stored provider not in registry: {exc}")
    state = secrets.token_urlsafe(32)
    verifier = pkce.generate_verifier(96)
    challenge = pkce.challenge_for(verifier)
    pending_auth.get_default_store().put(
        state,
        pending_auth.PendingAuthEntry(
            oauth_client_id=record.id,
            code_verifier=verifier,
            redirect_uri=decrypted_pd["redirect_uri"],
        ),
    )
    scopes = list(decrypted_pd.get("requested_scopes") or provider.default_scopes)
    authorize_url = _build_authorize_url(
        provider,
        client_id=decrypted_pd["client_id"],
        redirect_uri=decrypted_pd["redirect_uri"],
        scopes=scopes,
        state=state,
        code_challenge=challenge,
    )
    return {"authorize_url": authorize_url, "state": state}


@_oauth2_router.get("/callback")
async def _callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Vendor redirect lands here; exchange code → persist tokens."""
    if not state:
        return _html_page(
            "Missing state parameter. Authorization session is invalid; please try again.",
            status_code=400,
        )
    entry = pending_auth.get_default_store().pop(state)
    if entry is None:
        return _html_page(
            "Authorization session expired or invalid. Please try again from Connections.",
            status_code=410,
        )
    if error:
        return _html_page(
            f"Authorization cancelled or failed: {error}. You can close this window and try again.",
            status_code=200,
        )
    if not code:
        return _html_page(
            "Missing authorization code. Please retry from Connections.",
            status_code=400,
        )

    ctx = _get_ctx()
    record = ctx.store.get(entry.oauth_client_id)
    if record is None:
        return _html_page(
            f"Connection {entry.oauth_client_id!r} was deleted before the callback completed. "
            "Please re-register.",
            status_code=400,
        )
    decrypted_pd = ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    try:
        provider = oauth_providers.get_provider(decrypted_pd["provider"])
    except oauth_providers.UnknownProviderError as exc:
        return _html_page(f"Stored provider not in registry: {exc}", status_code=500)

    try:
        token_response = await exchange.exchange_code(
            provider,
            client_id=decrypted_pd["client_id"],
            client_secret=decrypted_pd["client_secret"],
            code=code,
            redirect_uri=entry.redirect_uri,
            code_verifier=entry.code_verifier,
        )
    except OAuthCallbackError as exc:
        ctx.store.update(
            record.id,
            status="error",
            last_error={"code": exc.code, "message": str(exc), "at": _utcnow_iso()},
        )
        return _html_page(f"Token exchange failed: {exc}", status_code=500)

    access_token = token_response.get("access_token")
    if not access_token:
        return _html_page("Vendor response missing access_token.", status_code=500)
    now = datetime.now(timezone.utc)
    expires_in = token_response.get("expires_in", 3600)
    try:
        expires_in_int = int(expires_in)
    except (TypeError, ValueError):
        expires_in_int = 3600
    expires_at = (now + timedelta(seconds=expires_in_int)).isoformat()
    scope_str = token_response.get("scope") or ""
    if scope_str:
        granted_scopes = [s for s in scope_str.split(provider.scope_separator) if s]
    else:
        granted_scopes = list(decrypted_pd.get("requested_scopes") or [])
    refresh_token = token_response.get("refresh_token")
    new_pd = {
        **decrypted_pd,
        "granted_scopes": granted_scopes,
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token_response.get("token_type", "Bearer"),
            "expires_at": expires_at,
            "obtained_at": now.isoformat(),
            "last_refreshed_at": None,
        },
    }
    encrypted_pd = ctx.router.encrypt(
        new_pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    ctx.store.update(record.id, protocol_data=encrypted_pd, status="authorized")
    return _html_page(
        f"{record.display_name} connected. Granted scopes: "
        f"{', '.join(granted_scopes) or '(none)'}. You can close this window.",
        status_code=200,
    )


@_oauth2_router.post("/{connection_id}/refresh")
async def _refresh(connection_id: str) -> dict:
    """Force token refresh via the vendor token endpoint."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "oauth2":
        raise HTTPException(
            400, f"connection {connection_id} is not oauth2 (got {record.protocol})"
        )
    decrypted_pd = ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    tokens = decrypted_pd.get("tokens") or {}
    refresh_token_value = tokens.get("refresh_token")
    if not refresh_token_value:
        raise HTTPException(
            400, "no refresh_token stored; re-authorize via /start",
        )
    try:
        provider = oauth_providers.get_provider(decrypted_pd["provider"])
    except oauth_providers.UnknownProviderError as exc:
        raise HTTPException(500, f"stored provider not in registry: {exc}")

    try:
        response = await exchange.refresh_access_token(
            provider,
            client_id=decrypted_pd["client_id"],
            client_secret=decrypted_pd["client_secret"],
            refresh_token=refresh_token_value,
        )
    except OAuthRefreshError as exc:
        ctx.store.update(
            record.id,
            status="expired",
            last_error={"code": exc.code, "message": str(exc), "at": _utcnow_iso()},
        )
        _emit_refresh_event(
            success=False,
            connection_id=record.id,
            project_id=record.project_id,
            provider=decrypted_pd.get("provider"),
            trigger="explicit",
            error_code=exc.code,
            error_message=str(exc),
        )
        raise HTTPException(
            400, detail={"code": exc.code, "message": str(exc)},
        )

    now = datetime.now(timezone.utc)
    old_expires_at = tokens.get("expires_at")
    new_access = response.get("access_token") or tokens.get("access_token")
    new_refresh = response.get("refresh_token") or refresh_token_value
    expires_in = response.get("expires_in", 3600)
    try:
        expires_in_int = int(expires_in)
    except (TypeError, ValueError):
        expires_in_int = 3600
    new_expires_at = (now + timedelta(seconds=expires_in_int)).isoformat()
    scope_str = response.get("scope")
    if scope_str:
        granted_scopes = [s for s in scope_str.split(provider.scope_separator) if s]
    else:
        granted_scopes = list(decrypted_pd.get("granted_scopes") or [])
    new_pd = {
        **decrypted_pd,
        "granted_scopes": granted_scopes,
        "tokens": {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": response.get("token_type", tokens.get("token_type", "Bearer")),
            "expires_at": new_expires_at,
            "obtained_at": tokens.get("obtained_at") or now.isoformat(),
            "last_refreshed_at": now.isoformat(),
            "refresh_count": int(tokens.get("refresh_count", 0)) + 1,
        },
    }
    encrypted_pd = ctx.router.encrypt(
        new_pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    updated = ctx.store.update(record.id, protocol_data=encrypted_pd, status="authorized")
    _emit_refresh_event(
        success=True,
        connection_id=record.id,
        project_id=record.project_id,
        provider=decrypted_pd.get("provider"),
        trigger="explicit",
        old_expires_at=old_expires_at,
        new_expires_at=new_expires_at,
    )
    # Return the masked view (don't leak fresh tokens back to caller).
    plugin = OAuth2ProtocolPlugin()
    return {
        "id": updated.id,
        "status": updated.status,
        "protocol_data": plugin.public_view(new_pd),
    }


@_oauth2_router.post("/{connection_id}/revoke")
async def _revoke(connection_id: str) -> dict:
    """Best-effort vendor revoke + clear local tokens."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "oauth2":
        raise HTTPException(
            400, f"connection {connection_id} is not oauth2 (got {record.protocol})"
        )
    decrypted_pd = ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    try:
        provider = oauth_providers.get_provider(decrypted_pd["provider"])
    except oauth_providers.UnknownProviderError:
        provider = None

    tokens = decrypted_pd.get("tokens") or {}
    access_token_value = tokens.get("access_token")
    if (
        provider is not None
        and provider.revoke_url
        and access_token_value
    ):
        try:
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                await http_client.post(
                    provider.revoke_url,
                    data={
                        "token": access_token_value,
                        "token_type_hint": "access_token",
                    },
                )
        except Exception:  # noqa: BLE001
            # Swallow vendor revoke failures — local clear still happens.
            pass

    new_pd = {**decrypted_pd, "tokens": None, "granted_scopes": []}
    encrypted_pd = ctx.router.encrypt(
        new_pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    updated = ctx.store.update(record.id, protocol_data=encrypted_pd, status="revoked")
    plugin = OAuth2ProtocolPlugin()
    return {
        "id": updated.id,
        "status": updated.status,
        "protocol_data": plugin.public_view(new_pd),
    }


# ── extra_cli implementations ───────────────────────────────────────


def _cli_load_connection(connection_id: str):
    """CLI helper: get connection or exit with error."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    if record.protocol != "oauth2":
        click.echo(
            f"  ✗ connection {connection_id!r} is not oauth2 (got {record.protocol!r})",
            err=True,
        )
        raise click.exceptions.Exit(4)
    return ctx, record


@click.command("start")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def _cli_start(connection_id: str, as_json: bool) -> None:
    """Mint authorize URL for an existing oauth2 connection (no loopback)."""
    import asyncio

    async def _run() -> dict:
        return await _start_authorize(connection_id)

    try:
        result = asyncio.run(_run())
    except HTTPException as exc:
        click.echo(f"  ✗ {exc.detail}", err=True)
        raise click.exceptions.Exit(exc.status_code // 100)
    if as_json:
        click.echo(_json.dumps(result, indent=2))
    else:
        click.echo(f"authorize_url: {result['authorize_url']}")
        click.echo(f"state: {result['state']}")


@click.command("refresh")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def _cli_refresh(connection_id: str, as_json: bool) -> None:
    """Force-refresh tokens for an oauth2 connection."""
    import asyncio

    async def _run() -> dict:
        return await _refresh(connection_id)

    try:
        result = asyncio.run(_run())
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            click.echo(f"  ✗ {detail.get('message', detail)}", err=True)
        else:
            click.echo(f"  ✗ {detail}", err=True)
        raise click.exceptions.Exit(exc.status_code // 100)
    if as_json:
        click.echo(_json.dumps(result, indent=2))
    else:
        click.echo(f"  ✓ refreshed {result['id']} (status={result['status']})")


@click.command("revoke")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def _cli_revoke(connection_id: str, as_json: bool) -> None:
    """Revoke vendor token (if supported) + clear local tokens."""
    import asyncio

    async def _run() -> dict:
        return await _revoke(connection_id)

    try:
        result = asyncio.run(_run())
    except HTTPException as exc:
        click.echo(f"  ✗ {exc.detail}", err=True)
        raise click.exceptions.Exit(exc.status_code // 100)
    if as_json:
        click.echo(_json.dumps(result, indent=2))
    else:
        click.echo(f"  ✓ revoked {result['id']} (status={result['status']})")


@click.command("reauthorize")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def _cli_reauthorize(connection_id: str, as_json: bool) -> None:
    """Re-run authorize-URL flow for an existing connection (no loopback)."""
    # Reauthorize is equivalent to start in this stateless CLI surface;
    # the loopback flow from PR #356 is not ported (operators open the
    # printed authorize_url manually).
    import asyncio

    async def _run() -> dict:
        return await _start_authorize(connection_id)

    try:
        result = asyncio.run(_run())
    except HTTPException as exc:
        click.echo(f"  ✗ {exc.detail}", err=True)
        raise click.exceptions.Exit(exc.status_code // 100)
    if as_json:
        click.echo(_json.dumps(result, indent=2))
    else:
        click.echo(f"authorize_url: {result['authorize_url']}")
        click.echo(f"state: {result['state']}")


@click.command("providers")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def _cli_providers(as_json: bool) -> None:
    """List supported OAuth providers (spotify, google, ...)."""
    rows = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "pkce_required": p.pkce_required,
            "docs_url": p.docs_url,
        }
        for p in oauth_providers.all_providers()
    ]
    if as_json:
        click.echo(_json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']}\t{row['display_name']}\t{row['docs_url']}")


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
        # Best-effort vendor revoke (if provider has revoke_url + tokens present).
        try:
            decrypted_pd = ctx.creds.decrypt(
                connection.protocol_data,
                sensitive_field_paths=self.sensitive_fields,
                connection_credentials_backend=connection.credentials_backend,
            ) if ctx.creds is not None else connection.protocol_data
        except Exception:
            return None
        try:
            provider = oauth_providers.get_provider(decrypted_pd.get("provider", ""))
        except oauth_providers.UnknownProviderError:
            return None
        tokens = decrypted_pd.get("tokens") or {}
        access_token_value = tokens.get("access_token")
        if provider.revoke_url and access_token_value:
            try:
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    await http_client.post(
                        provider.revoke_url,
                        data={
                            "token": access_token_value,
                            "token_type_hint": "access_token",
                        },
                    )
            except Exception:  # noqa: BLE001
                pass
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

    # ── Executor helper (replaces vault.find_default_client) ────────

    @staticmethod
    async def get_default_access_token(
        *,
        store,
        router,
        project_id: str | None,
        provider: str,
        trigger: str = "api_call",
    ) -> tuple[str, dict[str, Any]]:
        """Find the default oauth2 connection for ``(project_id, provider)`` and return its access token.

        Refreshes pre-emptively if ``expires_at - now < 60s``. Raises
        typed OAuth errors from :mod:`sagewai.oauth.errors`.

        Returns a tuple ``(access_token, connection_dict)`` where
        ``connection_dict`` is the decrypted record shape with the
        most recent tokens (so callers can read ``granted_scopes``,
        ``tokens.refresh_token``, etc. without a second lookup).
        """
        candidates = [
            c for c in store.list(project_id, protocol="oauth2")
            if c.protocol_data.get("provider") == provider and c.is_default
        ]
        if not candidates:
            raise OAuthNotConfiguredError(
                f"no default oauth2 connection for provider {provider!r} "
                f"in project {project_id!r}"
            )
        connection = candidates[0]
        if connection.status != "authorized":
            raise OAuthNotAuthorizedError(
                f"connection {connection.id} status is {connection.status!r}; not authorized"
            )
        decrypted_pd = router.decrypt(
            connection.protocol_data,
            sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
            connection_credentials_backend=connection.credentials_backend,
        )
        tokens = decrypted_pd.get("tokens") or {}
        if not tokens.get("access_token"):
            raise OAuthNotAuthorizedError(f"connection {connection.id} has no tokens")
        # Pre-emptive refresh window
        expires_at_raw = tokens.get("expires_at")
        if expires_at_raw:
            try:
                if expires_at_raw.endswith("Z"):
                    expires_at = datetime.fromisoformat(expires_at_raw[:-1] + "+00:00")
                else:
                    expires_at = datetime.fromisoformat(expires_at_raw)
            except ValueError:
                expires_at = None
            if expires_at is not None and expires_at - datetime.now(timezone.utc) < timedelta(seconds=60):
                try:
                    provider_obj = oauth_providers.get_provider(provider)
                except oauth_providers.UnknownProviderError as exc:
                    raise OAuthRefreshError(
                        f"stored provider not in registry: {exc}"
                    ) from exc
                try:
                    response = await exchange.refresh_access_token(
                        provider_obj,
                        client_id=decrypted_pd["client_id"],
                        client_secret=decrypted_pd["client_secret"],
                        refresh_token=tokens.get("refresh_token"),
                    )
                except OAuthRefreshError as exc:
                    store.update(
                        connection.id,
                        status="expired",
                        last_error={
                            "code": exc.code,
                            "message": str(exc),
                            "at": _utcnow_iso(),
                        },
                    )
                    _emit_refresh_event(
                        success=False,
                        connection_id=connection.id,
                        project_id=connection.project_id,
                        provider=provider,
                        trigger=trigger,
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                    raise
                now = datetime.now(timezone.utc)
                old_expires_at = tokens.get("expires_at")
                expires_in = int(response.get("expires_in", 3600))
                new_expires_at = (now + timedelta(seconds=expires_in)).isoformat()
                new_tokens = {
                    "access_token": response["access_token"],
                    "refresh_token": response.get("refresh_token") or tokens.get("refresh_token"),
                    "token_type": response.get("token_type", "Bearer"),
                    "expires_at": new_expires_at,
                    "obtained_at": tokens.get("obtained_at") or now.isoformat(),
                    "last_refreshed_at": now.isoformat(),
                    "refresh_count": int(tokens.get("refresh_count", 0)) + 1,
                }
                new_pd = {**decrypted_pd, "tokens": new_tokens}
                encrypted_pd = router.encrypt(
                    new_pd,
                    sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
                    connection_credentials_backend=connection.credentials_backend,
                )
                store.update(connection.id, protocol_data=encrypted_pd, status="authorized")
                _emit_refresh_event(
                    success=True,
                    connection_id=connection.id,
                    project_id=connection.project_id,
                    provider=provider,
                    trigger=trigger,
                    old_expires_at=old_expires_at,
                    new_expires_at=new_expires_at,
                )
                return new_tokens["access_token"], {
                    "id": connection.id,
                    "protocol_data": new_pd,
                    "granted_scopes": new_pd.get("granted_scopes", []),
                    "status": "authorized",
                    "credentials_backend": connection.credentials_backend,
                }
        return tokens["access_token"], {
            "id": connection.id,
            "protocol_data": decrypted_pd,
            "granted_scopes": decrypted_pd.get("granted_scopes", []),
            "status": connection.status,
            "credentials_backend": connection.credentials_backend,
        }


__all__ = [
    "OAuth2ProtocolData",
    "OAuth2ProtocolPlugin",
    "oauth2_default_key",
]
