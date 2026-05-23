# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin routes for /api/v1/admin/connections/oauth/*.

The OAuth surface mirrors the inference + tool connection vault but
adds the authorize-flow dance: client registration creates a pending
record; ``POST /{id}/start`` returns the vendor authorize URL and
stashes a CSRF + PKCE entry in the in-process
:class:`~sagewai.oauth.pending_auth.PendingAuthStore`; ``GET /callback``
is hit by the vendor browser redirect, exchanges code for tokens, and
flips the record to ``status='authorized'``.

Every route except ``/callback`` requires the admin session cookie.
``/callback`` is hit by the vendor — there's no cookie on that request,
so authentication is implicit via the single-use ``state`` nonce
written at start time.

Project scope: ``X-Project-ID`` header (or ``project_id`` query param);
``None`` is the org-global scope.
"""
from __future__ import annotations

import datetime
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from sagewai.admin.state_file import AdminStateFile
from sagewai.oauth import pending_auth, vault
from sagewai.oauth.errors import OAuthCallbackError, OAuthRefreshError
from sagewai.oauth.exchange import exchange_code, refresh_access_token
from sagewai.oauth.pkce import challenge_for, generate_verifier
from sagewai.oauth.providers import (
    OAuthProvider,
    UnknownProviderError,
    all_providers,
    get_provider,
)
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.master_key import MasterKeyMissing, resolve_master_key

CALLBACK_PATH = "/api/v1/admin/connections/oauth/callback"


# ── Pydantic models ────────────────────────────────────────────────


class CreateClientPayload(BaseModel):
    """Request body for creating a new OAuth client."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    display_name: str = Field(min_length=1, max_length=200)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    requested_scopes: list[str] | None = None


# ── Helpers ────────────────────────────────────────────────────────


def _store_path() -> Path:
    """Resolve the OAuth vault path (same file as inference/tool kinds)."""
    return vault._store_path()  # type: ignore[attr-defined]


def _crypto() -> Crypto:
    """Resolve the Sealed master key. Raises HTTPException 503 if missing."""
    try:
        key, _ = resolve_master_key()
    except MasterKeyMissing as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Sealed master key is not configured. Run "
                "`sagewai admin sealed init` (or set SAGEWAI_MASTER_KEY) "
                "before using OAuth. " + str(exc)
            ),
        ) from None
    return Crypto(key)


def _project_scope(request: Request) -> str | None:
    pid = (
        request.headers.get("x-project-id")
        or request.query_params.get("project_id")
    )
    return pid if pid else None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _expires_at_from(expires_in: Any) -> str | None:
    """Compute ``expires_at`` from a vendor ``expires_in`` (seconds).

    Returns ``None`` if ``expires_in`` is missing or unparseable. Stores
    the absolute timestamp so the executor's read-side can decide
    freshness without re-parsing relative offsets.
    """
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(seconds=seconds)
    return expires.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_authorize_url(
    provider: OAuthProvider,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    """Construct the vendor authorize URL with PKCE and CSRF params.

    Google additionally takes ``access_type=offline`` and ``prompt=consent``
    so a refresh_token is reliably returned on every consent flow.
    """
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


def _start_dance(
    request: Request,
    record: dict,
) -> tuple[str, str]:
    """Mint state + verifier, stash pending-auth entry, build authorize URL.

    Returns ``(authorize_url, state)``. The caller passes the masked
    vault row (or freshly-created record) so we have access to
    ``provider``, ``client_id``, ``requested_scopes``, ``redirect_uri``,
    and the row id.
    """
    provider = get_provider(record["provider"])
    state = secrets.token_urlsafe(32)
    verifier = generate_verifier(96)
    challenge = challenge_for(verifier)

    entry = pending_auth.PendingAuthEntry(
        oauth_client_id=record["id"],
        code_verifier=verifier,
        redirect_uri=record["redirect_uri"],
    )
    pending_auth.get_default_store().put(state, entry)

    authorize_url = _build_authorize_url(
        provider,
        client_id=record["client_id"],
        redirect_uri=record["redirect_uri"],
        scopes=list(record.get("requested_scopes") or provider.default_scopes),
        state=state,
        code_challenge=challenge,
    )
    return authorize_url, state


def _callback_url_from(request: Request) -> str:
    """Build the absolute callback URL the vendor will redirect back to."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{CALLBACK_PATH}"


def _html(status_code: int, message: str) -> HTMLResponse:
    """Render a minimal centered HTML response for callback outcomes."""
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Sagewai OAuth</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;display:flex;"
        "align-items:center;justify-content:center;min-height:80vh;"
        "background:#fafafa;color:#222;margin:0;padding:1rem;}"
        "main{max-width:32rem;text-align:center;}"
        "p{font-size:1.05rem;line-height:1.5;}"
        "</style></head>"
        f"<body><main><p>{message}</p></main></body></html>"
    )
    return HTMLResponse(content=body, status_code=status_code)


# ── Router factory ─────────────────────────────────────────────────


def _build_router(sf: AdminStateFile) -> APIRouter:
    """Construct the OAuth admin router bound to a state file (for auth)."""
    from sagewai.admin.autopilot_routes import _require_auth

    router = APIRouter(
        prefix="/api/v1/admin/connections/oauth", tags=["oauth"],
    )

    # ── GET /providers ──────────────────────────────────────────────

    @router.get("/providers", response_model=None)
    async def list_providers(request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        return [
            {
                "id": p.id,
                "display_name": p.display_name,
                "default_scopes": list(p.default_scopes),
                "docs_url": p.docs_url,
            }
            for p in all_providers()
        ]

    # ── GET / ───────────────────────────────────────────────────────

    @router.get("/", response_model=None)
    async def list_clients(request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        pid = _project_scope(request)
        return vault.list_clients(_store_path(), pid)

    # ── POST / ──────────────────────────────────────────────────────

    @router.post("/", response_model=None)
    async def create_client_and_start(
        request: Request, payload: CreateClientPayload,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        try:
            provider = get_provider(payload.provider)
        except UnknownProviderError:
            raise HTTPException(
                status_code=404,
                detail={"provider": payload.provider, "error": "unknown_provider"},
            )

        crypto = _crypto()
        scopes = (
            payload.requested_scopes
            if payload.requested_scopes is not None
            else list(provider.default_scopes)
        )
        redirect_uri = _callback_url_from(request)
        pid = _project_scope(request)
        masked = vault.create_client(
            _store_path(),
            crypto,
            provider=provider.id,
            project_id=pid,
            display_name=payload.display_name,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            redirect_uri=redirect_uri,
            requested_scopes=scopes,
        )
        authorize_url, state = _start_dance(request, masked)
        return {
            "record": masked,
            "authorize_url": authorize_url,
            "state": state,
        }

    # ── GET /callback ───────────────────────────────────────────────
    # Declared BEFORE the /{client_id} wildcards so Starlette's
    # registration-order routing matches the static path first.
    #
    # No auth: vendor browser redirect with no cookie. The single-use
    # state nonce IS the auth — its presence in PendingAuthStore proves
    # this callback came from a flow the admin started.

    @router.get("/callback")
    async def handle_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        if not state:
            return _html(
                400,
                "Missing state parameter. Authorization session is invalid; "
                "please try again from the OAuth tab.",
            )

        entry = pending_auth.get_default_store().pop(state)
        if entry is None:
            return _html(
                410,
                "Authorization session expired or invalid. "
                "Please try again from the OAuth tab.",
            )

        if error:
            # Vendor reported error (e.g., access_denied). Record stays
            # pending so the operator can retry from the OAuth tab.
            return _html(
                200,
                f"Authorization cancelled or failed: {error}. "
                "You can close this window and try again.",
            )

        if not code:
            return _html(
                400,
                "Missing authorization code. Please retry from the OAuth tab.",
            )

        # Look up the client row + decrypt the secret for the exchange.
        try:
            crypto = _crypto()
        except HTTPException as exc:
            return _html(exc.status_code, str(exc.detail))

        row_with_secrets = vault.get_client_with_secrets(
            _store_path(), entry.oauth_client_id, crypto,
        )
        if row_with_secrets is None:
            return _html(
                400,
                "OAuth client record was deleted before the callback "
                "completed. Please re-register.",
            )

        try:
            provider = get_provider(row_with_secrets["provider"])
        except UnknownProviderError as exc:
            return _html(500, f"Stored provider not in registry: {exc}")

        try:
            response = await exchange_code(
                provider,
                client_id=row_with_secrets["client_id"],
                client_secret=row_with_secrets["client_secret"],
                code=code,
                redirect_uri=row_with_secrets["redirect_uri"],
                code_verifier=entry.code_verifier,
            )
        except OAuthCallbackError as exc:
            vault.update_status(
                _store_path(),
                entry.oauth_client_id,
                "error",
                last_error={
                    "code": exc.code,
                    "message": str(exc),
                    "at": _now_iso(),
                },
            )
            return _html(500, f"Token exchange failed: {exc}")

        # Parse vendor response.
        access_token = response.get("access_token")
        if not access_token:
            return _html(500, "Vendor response missing access_token.")
        refresh_token = response.get("refresh_token") or ""
        scope_str = response.get("scope") or ""
        granted_scopes = (
            [s for s in scope_str.split(provider.scope_separator) if s]
            if scope_str
            else list(row_with_secrets.get("requested_scopes") or [])
        )
        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": response.get("token_type", "Bearer"),
            "expires_at": _expires_at_from(response.get("expires_in")),
        }
        vault.update_tokens(
            _store_path(),
            crypto,
            entry.oauth_client_id,
            tokens=tokens,
            granted_scopes=granted_scopes,
            status="authorized",
        )
        return _html(
            200,
            f"{row_with_secrets['display_name']} connected. "
            f"Granted scopes: {', '.join(granted_scopes) or '(none)'}. "
            "You can close this window.",
        )

    # ── GET /{id} ───────────────────────────────────────────────────

    @router.get("/{client_id}", response_model=None)
    async def get_client(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        row = vault.get_client(_store_path(), client_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"id": client_id})
        return row

    # ── DELETE /{id} ────────────────────────────────────────────────

    @router.delete("/{client_id}", status_code=204, response_model=None)
    async def delete_client(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        ok = vault.delete_client(_store_path(), client_id)
        if not ok:
            raise HTTPException(status_code=404, detail={"id": client_id})
        return Response(status_code=204)

    # ── POST /{id}/start ────────────────────────────────────────────

    @router.post("/{client_id}/start", response_model=None)
    async def start_authorize(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        row = vault.get_client(_store_path(), client_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"id": client_id})
        try:
            authorize_url, state = _start_dance(request, row)
        except UnknownProviderError as exc:
            raise HTTPException(
                status_code=500, detail=f"stored provider not in registry: {exc}",
            )
        return {"authorize_url": authorize_url, "state": state}

    # ── POST /{id}/refresh ──────────────────────────────────────────

    @router.post("/{client_id}/refresh", response_model=None)
    async def force_refresh(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        crypto = _crypto()
        row = vault.get_client_with_secrets(_store_path(), client_id, crypto)
        if row is None:
            raise HTTPException(status_code=404, detail={"id": client_id})
        tokens = row.get("tokens") or {}
        refresh_token_value = tokens.get("refresh_token")
        if not refresh_token_value:
            raise HTTPException(
                status_code=400,
                detail="no refresh_token stored; re-authorize from the OAuth tab",
            )
        try:
            provider = get_provider(row["provider"])
        except UnknownProviderError as exc:
            raise HTTPException(
                status_code=500, detail=f"stored provider not in registry: {exc}",
            )

        try:
            response = await refresh_access_token(
                provider,
                client_id=row["client_id"],
                client_secret=row["client_secret"],
                refresh_token=refresh_token_value,
            )
        except OAuthRefreshError as exc:
            vault.update_status(
                _store_path(),
                client_id,
                "expired",
                last_error={
                    "code": exc.code,
                    "message": str(exc),
                    "at": _now_iso(),
                },
            )
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": str(exc)},
            )

        new_access = response.get("access_token") or tokens.get("access_token")
        # Vendors that don't rotate refresh_tokens (e.g., Spotify) omit
        # the field — fall back to the existing one in that case.
        new_refresh = response.get("refresh_token") or refresh_token_value
        scope_str = response.get("scope")
        if scope_str:
            granted_scopes = [
                s for s in scope_str.split(provider.scope_separator) if s
            ]
        else:
            granted_scopes = list(row.get("granted_scopes") or [])

        new_tokens = {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": response.get("token_type", tokens.get("token_type", "Bearer")),
            "expires_at": _expires_at_from(response.get("expires_in")),
        }
        return vault.update_tokens(
            _store_path(),
            crypto,
            client_id,
            tokens=new_tokens,
            granted_scopes=granted_scopes,
            status="authorized",
        )

    # ── POST /{id}/revoke ───────────────────────────────────────────

    @router.post("/{client_id}/revoke", response_model=None)
    async def revoke_client(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        crypto = _crypto()
        row = vault.get_client_with_secrets(_store_path(), client_id, crypto)
        if row is None:
            raise HTTPException(status_code=404, detail={"id": client_id})
        try:
            provider = get_provider(row["provider"])
        except UnknownProviderError as exc:
            raise HTTPException(
                status_code=500, detail=f"stored provider not in registry: {exc}",
            )

        # Best-effort vendor revoke. If the vendor's endpoint is down or
        # the token is already invalidated, we still clear local state —
        # the local record is the source of truth for the executor.
        if provider.revoke_url and (row.get("tokens") or {}).get("access_token"):
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    await http.post(
                        provider.revoke_url,
                        data={
                            "token": row["tokens"]["access_token"],
                            "token_type_hint": "access_token",
                        },
                    )
            except Exception:  # noqa: BLE001
                # Swallow vendor revoke failures — local clear still happens.
                pass

        return vault.clear_tokens(_store_path(), client_id)

    # ── POST /{id}/set-default ──────────────────────────────────────

    @router.post("/{client_id}/set-default", response_model=None)
    async def set_default_client(
        client_id: str, request: Request,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        row = vault.get_client(_store_path(), client_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"id": client_id})
        return vault.set_default(_store_path(), client_id)

    return router


# ── Wiring ─────────────────────────────────────────────────────────


def register(app: FastAPI, sf: AdminStateFile) -> None:
    """Mount the OAuth admin routes under /api/v1/admin/connections/oauth/*."""
    app.include_router(_build_router(sf))


__all__ = ["register"]
