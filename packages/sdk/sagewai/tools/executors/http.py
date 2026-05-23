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
from pathlib import Path
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from sagewai.oauth import vault as oauth_vault
from sagewai.oauth.errors import (
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
    OAuthScopeMissingError,
)
from sagewai.oauth.exchange import refresh_access_token
from sagewai.oauth.providers import get_provider
from sagewai.sealed.crypto import Crypto
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
# Two seams (``_resolve_store_path`` and ``_resolve_crypto``) wrap the only
# pieces of global state the oauth2 branch needs — the on-disk vault path and
# the master-key-derived Crypto instance. Tests monkeypatch these to isolate
# from the user's real ``~/.sagewai`` directory.


def _resolve_store_path() -> Path:
    """Resolve the credentials vault path. Indirection seam for tests."""
    return oauth_vault._store_path()


def _resolve_crypto() -> Crypto:
    """Resolve the master key + return a Crypto. Indirection seam for tests."""
    from sagewai.sealed.master_key import resolve_master_key

    key, _ = resolve_master_key()
    return Crypto(key)


def _parse_expires_at(expires_at: str) -> datetime:
    """Parse the ISO-8601 ``expires_at`` field. Accepts both ``+00:00`` and
    trailing-``Z`` notation."""
    if expires_at.endswith("Z"):
        expires_at = expires_at[:-1] + "+00:00"
    dt = datetime.fromisoformat(expires_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


async def _refresh_and_persist(
    client: dict[str, Any],
    crypto: Crypto,
    store_path: Path,
) -> dict[str, Any]:
    """Refresh the access token via the vendor token endpoint and persist
    the result. On failure mark the client ``expired`` and re-raise."""
    provider = get_provider(client["provider"])
    tokens = client["tokens"] or {}
    refresh_token = tokens.get("refresh_token")
    try:
        new = await refresh_access_token(
            provider,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            refresh_token=refresh_token,
        )
    except OAuthRefreshError as exc:
        now_iso = datetime.now(timezone.utc).isoformat()
        oauth_vault.update_status(
            store_path,
            client["id"],
            "expired",
            last_error={
                "code": "oauth_refresh_failed",
                "message": str(exc),
                "at": now_iso,
            },
        )
        raise

    now = datetime.now(timezone.utc)
    expires_in = int(new.get("expires_in", 3600))
    new_expires_at = (now + timedelta(seconds=expires_in)).isoformat()
    # Spotify (and others) may omit refresh_token on refresh — preserve prior.
    new_refresh = new.get("refresh_token", refresh_token)
    persisted_tokens = {
        "access_token": new["access_token"],
        "refresh_token": new_refresh,
        "token_type": new.get("token_type", tokens.get("token_type", "Bearer")),
        "expires_at": new_expires_at,
        "obtained_at": tokens.get("obtained_at") or now.isoformat(),
        "last_refreshed_at": now.isoformat(),
    }
    oauth_vault.update_tokens(
        store_path,
        crypto,
        client["id"],
        tokens=persisted_tokens,
        granted_scopes=client["granted_scopes"],
        status="authorized",
    )

    refreshed = oauth_vault.get_client_with_secrets(
        store_path, client["id"], crypto
    )
    assert refreshed is not None  # just persisted above
    return refreshed


async def _resolve_oauth_token(
    entry: CatalogEntry, project_id: str
) -> tuple[str, dict[str, Any]]:
    """Validate config + scopes, optionally pre-emptively refresh, return
    ``(access_token, client_record)``. The record is returned alongside so
    the reactive-refresh path can avoid a second vault lookup."""
    auth_cfg = entry.exec_["http"]["auth"]
    provider_id = auth_cfg["oauth_provider"]
    required_scopes = list(entry.setup.get("required_scopes", []))

    store_path = _resolve_store_path()
    crypto = _resolve_crypto()
    client = oauth_vault.find_default_client(
        store_path, crypto, project_id, provider_id
    )
    if client is None:
        raise OAuthNotConfiguredError(
            f"no OAuth client configured for provider {provider_id!r} in project {project_id!r}"
        )
    if client.get("status") != "authorized":
        raise OAuthNotAuthorizedError(
            f"OAuth client {client['id']!r} is in status {client.get('status')!r}; "
            "re-authorize via the admin UI"
        )

    granted = set(client.get("granted_scopes") or [])
    missing = set(required_scopes) - granted
    if missing:
        raise OAuthScopeMissingError(
            f"OAuth client missing required scopes: {sorted(missing)}"
        )

    tokens = client.get("tokens") or {}
    expires_at_raw = tokens.get("expires_at")
    if expires_at_raw:
        expires_at = _parse_expires_at(expires_at_raw)
        now = datetime.now(timezone.utc)
        if expires_at - now < timedelta(seconds=_REFRESH_BUFFER_SECONDS):
            client = await _refresh_and_persist(client, crypto, store_path)

    access_token = client["tokens"]["access_token"]
    return access_token, client


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
        store_path = _resolve_store_path()
        crypto = _resolve_crypto()
        # _refresh_and_persist raises OAuthRefreshError (and marks expired)
        # on failure — bubble that through.
        refreshed_client = await _refresh_and_persist(
            oauth_client, crypto, store_path
        )
        retry_headers = {
            "Authorization": f"Bearer {refreshed_client['tokens']['access_token']}"
        }
        resp = await _do_request(retry_headers)
        if resp.status_code == 401:
            # Refresh succeeded but the new token still fails — treat as
            # unrecoverable and surface the same expired-state signal.
            now_iso = datetime.now(timezone.utc).isoformat()
            oauth_vault.update_status(
                store_path,
                refreshed_client["id"],
                "expired",
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
