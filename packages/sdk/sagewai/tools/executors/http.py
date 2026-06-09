# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: http`` executor — REST/GraphQL SaaS APIs."""
from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from sagewai.oauth.errors import (
    OAuthNotAuthorizedError,  # noqa: F401  — re-exported for callers
    OAuthNotConfiguredError,  # noqa: F401
    OAuthRefreshError,
    OAuthScopeMissingError,
)
from sagewai.tools.registry import CatalogEntry

# Pre-emptive refresh window: if the stored access_token expires within this
# many seconds (or has already expired), refresh before the API call instead
# of paying a round-trip + 401 + refresh + retry.
_REFRESH_BUFFER_SECONDS = 60


class InputValidationError(ValueError):
    pass


class OutputValidationError(ValueError):
    pass


class UnknownOperationError(KeyError):
    pass


class AuthConfigurationError(RuntimeError):
    pass


def _validate(schema: dict | None, payload: Any, err_cls: type[Exception]) -> None:
    if not schema:
        return
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise err_cls("; ".join(e.message for e in errors))


def _format_path(template: str, inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Substitute ``{name}`` segments; remaining inputs become query/body params."""
    placeholders = {fname for _, fname, _, _ in string.Formatter().parse(template) if fname}
    missing = placeholders - inputs.keys()
    if missing:
        raise InputValidationError(f"missing path params: {sorted(missing)}")
    path = template.format(**{k: inputs[k] for k in placeholders})
    extras = {k: v for k, v in inputs.items() if k not in placeholders}
    return path, extras


def _build_auth_headers(auth_cfg: dict, creds: dict[str, str]) -> dict[str, str]:
    kind = auth_cfg["kind"]
    if kind == "none":
        return {}
    if kind in ("api_key", "bearer"):
        token = next((v for v in creds.values() if v), None)
        if not token:
            raise AuthConfigurationError("missing credential")
        return {auth_cfg.get("header", "Authorization"): f"{auth_cfg.get('prefix', '')}{token}"}
    if kind == "basic":
        import base64
        user = creds.get("USERNAME") or ""
        pw = creds.get("PASSWORD") or ""
        encoded = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    raise AuthConfigurationError(f"auth.kind {kind!r} not yet supported by this executor")


# ---------------------------------------------------------------------------
# oauth2 helpers
# ---------------------------------------------------------------------------
#
# PR4: the oauth2 branch consumes the Connections Platform plugin helper
# ``OAuth2ProtocolPlugin.get_default_access_token`` instead of the deleted
# ``sagewai.oauth.vault`` module. Scope validation and reactive refresh on
# 401 stay in this module.


def _build_default_connections_context():
    from sagewai.admin.tenancy import is_multi_tenant

    if is_multi_tenant():
        raise OAuthNotConfiguredError(
            "tenant-safe connection context required for OAuth tool execution"
        )
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
    from sagewai.connections.bootstrap import build_connections_context

    return build_connections_context(AdminStateFile(default_admin_state_path()))


def _has_insufficient_scope_marker(response: httpx.Response) -> bool:
    """RFC 6750 §3 / OAuth2 convention — ``WWW-Authenticate`` header signals
    insufficient scope on a 401. Some vendors only put it in the body, so check
    both."""
    auth_header = response.headers.get("WWW-Authenticate", "")
    if "insufficient_scope" in auth_header.lower():
        return True
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        return False
    if not isinstance(body, dict):
        return False
    err = str(body.get("error", "")).lower()
    return err == "insufficient_scope"


async def _resolve_oauth_token(
    entry: CatalogEntry, project_id: str
) -> tuple[str, dict[str, Any]]:
    """Validate config + scopes, optionally pre-emptively refresh, return
    ``(access_token, client_record)``. The record dict is returned alongside
    so the reactive-refresh path can avoid a second store lookup."""
    from sagewai.connections.protocols.oauth2 import OAuth2ProtocolPlugin

    auth_cfg = entry.exec_["http"]["auth"]
    provider_id = auth_cfg["oauth_provider"]
    required_scopes = list(entry.setup.get("required_scopes", []))

    ctx = _build_default_connections_context()
    # get_default_access_token does the default-lookup + decrypt + (if
    # within _REFRESH_BUFFER_SECONDS of expiry) eager refresh; raises
    # the typed OAuth errors for the executor's error handling.
    access_token, client = await OAuth2ProtocolPlugin.get_default_access_token(
        store=ctx.store,
        router=ctx.router,
        project_id=project_id,
        provider=provider_id,
    )
    granted = set(client.get("granted_scopes") or [])
    missing = set(required_scopes) - granted
    if missing:
        raise OAuthScopeMissingError(
            f"OAuth client missing required scopes: {sorted(missing)}"
        )
    return access_token, client


async def _refresh_via_plugin(
    client: dict[str, Any], project_id: str, provider_id: str
) -> dict[str, Any]:
    """Reactive refresh after a 401. Delegates to the plugin helper.

    Returns the freshened client dict (with new tokens). Raises
    :class:`OAuthRefreshError` on failure (the helper marks the
    connection ``expired`` internally).
    """
    from sagewai.connections.protocols.oauth2 import OAuth2ProtocolPlugin
    from sagewai.oauth import exchange as oauth_exchange
    from sagewai.oauth.providers import get_provider as _get_provider

    ctx = _build_default_connections_context()
    # Re-fetch the connection (its tokens may have been updated since the
    # initial _resolve_oauth_token call).
    connection_id = client.get("id")
    if connection_id is None:
        raise OAuthRefreshError("missing connection id for reactive refresh")
    record = ctx.store.get(connection_id)
    if record is None:
        raise OAuthRefreshError(f"connection {connection_id!r} disappeared")
    decrypted_pd = ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    tokens = decrypted_pd.get("tokens") or {}
    refresh_token_value = tokens.get("refresh_token")
    if not refresh_token_value:
        raise OAuthRefreshError(
            f"connection {connection_id!r} has no refresh_token"
        )
    provider = _get_provider(provider_id)
    try:
        response = await oauth_exchange.refresh_access_token(
            provider,
            client_id=decrypted_pd["client_id"],
            client_secret=decrypted_pd["client_secret"],
            refresh_token=refresh_token_value,
        )
    except OAuthRefreshError as exc:
        now_iso = datetime.now(timezone.utc).isoformat()
        ctx.store.update(
            connection_id,
            status="expired",
            last_error={
                "code": exc.code,
                "message": str(exc),
                "at": now_iso,
            },
        )
        raise
    now = datetime.now(timezone.utc)
    expires_in = int(response.get("expires_in", 3600))
    new_tokens = {
        "access_token": response["access_token"],
        "refresh_token": response.get("refresh_token") or refresh_token_value,
        "token_type": response.get("token_type", tokens.get("token_type", "Bearer")),
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "obtained_at": tokens.get("obtained_at") or now.isoformat(),
        "last_refreshed_at": now.isoformat(),
    }
    new_pd = {**decrypted_pd, "tokens": new_tokens}
    encrypted_pd = ctx.router.encrypt(
        new_pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )
    ctx.store.update(connection_id, protocol_data=encrypted_pd, status="authorized")
    return {
        "id": connection_id,
        "protocol_data": new_pd,
        "granted_scopes": new_pd.get("granted_scopes", []),
        "tokens": new_tokens,
        "status": "authorized",
        "credentials_backend": record.credentials_backend,
    }


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    http_cfg = entry.exec_["http"]
    auth_cfg = http_cfg["auth"]
    if operation not in http_cfg["operations"]:
        raise UnknownOperationError(operation)
    op = http_cfg["operations"][operation]

    _validate(op.get("input_schema"), inputs, InputValidationError)
    path, extras = _format_path(op["path"], inputs)

    # OAuth2 has its own headers + reactive-refresh path. Other auth kinds
    # (none / api_key / bearer / basic) flow through the legacy
    # _build_auth_headers helper and skip the 401-retry logic.
    is_oauth2 = auth_cfg.get("kind") == "oauth2"

    creds: dict[str, str] = {}
    oauth_client: dict[str, Any] | None = None
    if is_oauth2:
        access_token, oauth_client = await _resolve_oauth_token(entry, project_id)
        headers = {"Authorization": f"Bearer {access_token}"}
    else:
        creds = get_credentials(project_id=project_id, kind="tool", id=entry.id)
        headers = _build_auth_headers(auth_cfg, creds)

    base_url = http_cfg["base_url"]
    runtime_field = http_cfg.get("runtime_base_url_field")
    if runtime_field:
        # OAuth2 entries don't use the tool credential vault, so creds is {}
        # here. runtime_base_url_field is currently only used by api_key
        # tools; leave the lookup in place for symmetry.
        override = creds.get(runtime_field) if creds else None
        if override:
            base_url = override
    url = base_url.rstrip("/") + path
    method = op["method"].upper()
    body_format = op.get("body_format", "json")

    async def _do_request(req_headers: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            if method == "GET":
                return await client.get(url, headers=req_headers, params=extras or None)
            if body_format == "form":
                return await client.request(method, url, headers=req_headers, data=extras or None)
            return await client.request(method, url, headers=req_headers, json=extras or None)

    resp = await _do_request(headers)

    if is_oauth2 and resp.status_code == 401:
        # Distinguish insufficient_scope (no refresh — operator must
        # re-authorize with broader scopes) from expired_token (refresh +
        # retry once).
        if _has_insufficient_scope_marker(resp):
            raise OAuthScopeMissingError(
                "vendor returned 401 insufficient_scope; "
                "re-authorize the OAuth client with broader scopes"
            )

        assert oauth_client is not None  # set above whenever is_oauth2
        # _refresh_via_plugin raises OAuthRefreshError (and marks the
        # connection ``expired``) on failure — bubble that through.
        refreshed_client = await _refresh_via_plugin(
            oauth_client, project_id, auth_cfg["oauth_provider"]
        )
        retry_headers = {
            "Authorization": f"Bearer {refreshed_client['tokens']['access_token']}"
        }
        resp = await _do_request(retry_headers)
        if resp.status_code == 401:
            # Refresh succeeded but the new token still fails — treat as
            # unrecoverable and surface the same expired-state signal.
            ctx = _build_default_connections_context()
            now_iso = datetime.now(timezone.utc).isoformat()
            ctx.store.update(
                refreshed_client["id"],
                status="expired",
                last_error={
                    "code": "oauth_refresh_failed",
                    "message": "vendor returned 401 after refresh + retry",
                    "at": now_iso,
                },
            )
            raise OAuthRefreshError(
                "vendor returned 401 after refresh + retry; client marked expired"
            )

    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    _validate(op.get("output_schema"), payload, OutputValidationError)
    return payload
