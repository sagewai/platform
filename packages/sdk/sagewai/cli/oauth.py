# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sagewai oauth — manage OAuth client credentials from the CLI.

Subcommands:

  providers          List supported OAuth providers from the registry
  list               List OAuth client records for the current project
  status <id>        Show one client's detail
  add <provider>     Register + interactively authorize a new client
                     (binds a loopback HTTP listener to receive the
                     redirect, mints PKCE + state, opens the browser)
  refresh <id>       Force a refresh against the vendor token endpoint
  revoke <id>        Revoke vendor token (if supported) + clear local
  delete <id>        Hard-delete a client record
  reauthorize <id>   Re-run the loopback flow for an existing record
  set-default <id>   Mark a client as the default for its (project,
                     provider) pair

The interactive ``add`` flow opens a short-lived HTTPServer on
``127.0.0.1:<port>``, opens the operator's browser at the vendor
authorize URL, and waits for the vendor's redirect on ``/callback``.
The handler captures ``?code=…&state=…``, the main thread validates
``state``, exchanges the code via :func:`sagewai.oauth.exchange.exchange_code`,
and persists the resulting tokens through :mod:`sagewai.oauth.vault`.

All vault accesses go through :func:`sagewai.oauth.vault._store_path` so
the ``SAGEWAI_ADMIN_STATE_FILE`` env override works the same way it does
for admin tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import click
import httpx

from sagewai.oauth import vault
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


# ── Helpers ────────────────────────────────────────────────────────


def _default_project() -> str:
    """Resolve the project scope. ``SAGEWAI_PROJECT`` env, else ``default``."""
    return os.environ.get("SAGEWAI_PROJECT") or "default"


def _crypto() -> Crypto:
    """Resolve the Sealed master key into a :class:`Crypto` instance."""
    try:
        key, _ = resolve_master_key()
    except MasterKeyMissing as exc:
        click.echo(
            "  ✗ Sealed master key is not configured. Run "
            "`sagewai admin sealed init` or set SAGEWAI_MASTER_KEY.",
            err=True,
        )
        raise click.exceptions.Exit(2) from exc
    return Crypto(key)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_at_from(expires_in: Any) -> str | None:
    """Compute absolute ``expires_at`` from a relative ``expires_in`` (seconds)."""
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _build_authorize_url(
    provider: OAuthProvider,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    """Construct the vendor authorize URL with PKCE and CSRF params."""
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


def _echo_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Print a tiny ASCII table (header row + dashes + rows)."""
    if not rows:
        click.echo("(no records)")
        return
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = max(
            len(col),
            max((len(str(row.get(col, ""))) for row in rows), default=0),
        )
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    click.echo(header)
    click.echo("-" * len(header))
    for row in rows:
        click.echo("  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


# ── Loopback callback listener ─────────────────────────────────────


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures one GET /callback?... and stores the query into the server.

    Suppresses BaseHTTPRequestHandler's default stderr logging so it
    doesn't pollute Click's captured stdout/stderr in tests.
    """

    # Suppress the access-log noise that BaseHTTPRequestHandler
    # otherwise sends to stderr.
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802 (stdlib signature)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.endswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        captured: dict[str, str] = {}
        for key in ("code", "state", "error", "error_description"):
            if key in qs:
                captured[key] = qs[key][0]
        # Stash on the server instance for the main thread.
        self.server.captured = captured  # type: ignore[attr-defined]
        self.server.callback_event.set()  # type: ignore[attr-defined]

        body = (
            b"<!doctype html><html><head><meta charset='utf-8'>"
            b"<title>Sagewai OAuth</title></head>"
            b"<body style='font-family:system-ui,sans-serif;"
            b"text-align:center;padding-top:3rem'>"
            b"<p>Authorization complete. You can close this window.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _run_loopback_callback(
    *,
    authorize_url: str,
    port: int,
    state: str,
    timeout: float,
) -> tuple[str, str] | None:
    """Run a one-shot HTTP listener on 127.0.0.1:<port>, wait for /callback.

    Returns ``(code, state)`` on success, ``None`` on timeout or
    error-response from the vendor (the latter is logged to stderr).

    This function is the seam tests monkeypatch — see
    ``tests/cli/test_oauth_cli.py``. In production, it binds a real
    socket and blocks the calling thread until either the vendor's
    redirect arrives or ``timeout`` seconds elapse.
    """
    # authorize_url is unused in production beyond logging; the caller
    # is responsible for opening the browser. Keep it in the signature
    # so tests can verify the URL passed in matches the one printed.
    del authorize_url

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.captured = None  # type: ignore[attr-defined]
    server.callback_event = threading.Event()  # type: ignore[attr-defined]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        if not server.callback_event.wait(timeout=timeout):  # type: ignore[attr-defined]
            return None
        captured: dict[str, str] = server.captured or {}  # type: ignore[attr-defined]
        if captured.get("error"):
            click.echo(
                f"  ✗ Vendor returned error: {captured['error']} "
                f"{captured.get('error_description', '')}",
                err=True,
            )
            return None
        code = captured.get("code")
        returned_state = captured.get("state")
        if not code or not returned_state:
            return None
        if returned_state != state:
            click.echo("  ✗ State mismatch — possible CSRF attempt.", err=True)
            return None
        return (code, returned_state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ── Group + commands ───────────────────────────────────────────────


@click.group("oauth")
def oauth() -> None:
    """Manage OAuth client credentials (Spotify, Google, ...)."""


# providers ---------------------------------------------------------


@oauth.command("providers")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def providers_cmd(as_json: bool) -> None:
    """List supported OAuth providers from the registry."""
    rows = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "pkce_required": p.pkce_required,
            "docs_url": p.docs_url,
        }
        for p in all_providers()
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    _echo_table(rows, ["id", "display_name", "pkce_required", "docs_url"])


# list --------------------------------------------------------------


@oauth.command("list")
@click.option("--project", default=None, help="Project scope (default: $SAGEWAI_PROJECT or 'default')")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_cmd(project: str | None, as_json: bool) -> None:
    """List OAuth client records for the current project."""
    pid = project or _default_project()
    records = vault.list_clients(vault._store_path(), pid)
    if as_json:
        click.echo(json.dumps(records, indent=2, default=str))
        return
    table_rows: list[dict[str, Any]] = []
    for r in records:
        tokens = r.get("tokens") or {}
        table_rows.append(
            {
                "id": r["id"],
                "provider": r["provider"],
                "display_name": r["display_name"],
                "status": r["status"],
                "is_default": r["is_default"],
                "expires_at": tokens.get("expires_at") or "—",
            }
        )
    _echo_table(
        table_rows,
        ["id", "provider", "display_name", "status", "is_default", "expires_at"],
    )


# status ------------------------------------------------------------


@oauth.command("status")
@click.argument("client_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status_cmd(client_id: str, as_json: bool) -> None:
    """Show one client's detail."""
    record = vault.get_client(vault._store_path(), client_id)
    if record is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    if as_json:
        click.echo(json.dumps(record, indent=2, default=str))
        return
    # Human-readable: key/value lines
    for key in (
        "id",
        "provider",
        "display_name",
        "project_id",
        "status",
        "is_default",
        "redirect_uri",
        "requested_scopes",
        "granted_scopes",
        "created_at",
        "updated_at",
    ):
        click.echo(f"{key}: {record.get(key)}")
    tokens = record.get("tokens") or {}
    if tokens:
        click.echo(f"tokens.expires_at: {tokens.get('expires_at')}")
        click.echo(f"tokens.token_type: {tokens.get('token_type')}")


# add / reauthorize -------------------------------------------------


def _run_authorize_dance(
    provider: OAuthProvider,
    *,
    client_id_value: str,
    client_secret_value: str,
    scopes: list[str],
    redirect_port: int,
    timeout: int,
) -> tuple[str, list[str], dict[str, Any]]:
    """Drive the loopback authorize flow end-to-end.

    Returns ``(verifier, granted_scopes, tokens_dict_to_persist)``. Raises
    :class:`click.exceptions.Exit` on any failure (timeout, vendor error,
    state mismatch, exchange failure).
    """
    state = secrets.token_urlsafe(32)
    verifier = generate_verifier()
    challenge = challenge_for(verifier)
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"

    authorize_url = _build_authorize_url(
        provider,
        client_id=client_id_value,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
        code_challenge=challenge,
    )

    click.echo(
        "\nOpen this URL in your browser to authorize:\n\n"
        f"  {authorize_url}\n\n"
        f"Waiting for callback on http://127.0.0.1:{redirect_port}...\n"
    )

    # Best-effort browser open. Headless / CI environments stay silent.
    try:
        webbrowser.open(authorize_url)
    except Exception:  # noqa: BLE001
        pass

    result = _run_loopback_callback(
        authorize_url=authorize_url,
        port=redirect_port,
        state=state,
        timeout=float(timeout),
    )
    if result is None:
        click.echo(
            "  ✗ Authorization timed out or failed. "
            "Use `sagewai oauth reauthorize <id>` to retry.",
            err=True,
        )
        raise click.exceptions.Exit(3)

    code, _returned_state = result

    try:
        response = asyncio.run(
            exchange_code(
                provider,
                client_id=client_id_value,
                client_secret=client_secret_value,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=verifier,
            )
        )
    except OAuthCallbackError as exc:
        click.echo(f"  ✗ Token exchange failed: {exc}", err=True)
        raise click.exceptions.Exit(3) from exc

    access_token = response.get("access_token")
    if not access_token:
        click.echo("  ✗ Vendor response missing access_token.", err=True)
        raise click.exceptions.Exit(3)
    refresh_token = response.get("refresh_token") or ""
    scope_str = response.get("scope") or ""
    granted_scopes = (
        [s for s in scope_str.split(provider.scope_separator) if s]
        if scope_str
        else list(scopes)
    )
    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": response.get("token_type", "Bearer"),
        "expires_at": _expires_at_from(response.get("expires_in")),
    }
    return verifier, granted_scopes, tokens


@oauth.command("add")
@click.argument("provider_id")
@click.option("--display-name", default=None, help="Friendly label for this client")
@click.option("--scopes", default=None, help="Space-separated scope list (default: provider defaults)")
@click.option("--redirect-port", default=53682, type=int, help="Local port for the callback listener")
@click.option("--timeout", default=300, type=int, help="Seconds to wait for the vendor callback")
@click.option("--project", default=None, help="Project scope (default: $SAGEWAI_PROJECT or 'default')")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def add_cmd(
    provider_id: str,
    display_name: str | None,
    scopes: str | None,
    redirect_port: int,
    timeout: int,
    project: str | None,
    as_json: bool,
) -> None:
    """Register + interactively authorize a new OAuth client."""
    try:
        provider = get_provider(provider_id)
    except UnknownProviderError:
        click.echo(f"  ✗ Unknown provider {provider_id!r}", err=True)
        raise click.exceptions.Exit(2) from None

    pid = project or _default_project()
    final_display = display_name or click.prompt(
        "Display name", default=provider.display_name
    )
    client_id_value = click.prompt("Client ID", hide_input=True)
    client_secret_value = click.prompt("Client Secret", hide_input=True)

    if scopes is None:
        default_scope_str = provider.scope_separator.join(provider.default_scopes)
        scopes = click.prompt("Scopes (space-separated)", default=default_scope_str)
    scope_list = [s for s in scopes.split() if s]

    crypto = _crypto()
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"

    # Persist the pending record first so an interrupted authorize leaves
    # a row the operator can `reauthorize` against.
    masked = vault.create_client(
        vault._store_path(),
        crypto,
        provider=provider.id,
        project_id=pid,
        display_name=final_display,
        client_id=client_id_value,
        client_secret=client_secret_value,
        redirect_uri=redirect_uri,
        requested_scopes=scope_list,
    )

    _, granted_scopes, tokens = _run_authorize_dance(
        provider,
        client_id_value=client_id_value,
        client_secret_value=client_secret_value,
        scopes=scope_list,
        redirect_port=redirect_port,
        timeout=timeout,
    )

    updated = vault.update_tokens(
        vault._store_path(),
        crypto,
        masked["id"],
        tokens=tokens,
        granted_scopes=granted_scopes,
        status="authorized",
    )
    if as_json:
        click.echo(json.dumps(updated, indent=2, default=str))
        return
    click.echo(
        f"\n  ✓ Authorized {updated['display_name']!r} (id={updated['id']!r})"
    )
    click.echo(f"  granted_scopes: {', '.join(granted_scopes) or '(none)'}")
    if tokens.get("expires_at"):
        click.echo(f"  expires_at: {tokens['expires_at']}")


@oauth.command("reauthorize")
@click.argument("client_id")
@click.option("--redirect-port", default=53682, type=int)
@click.option("--timeout", default=300, type=int)
@click.option("--json", "as_json", is_flag=True)
def reauthorize_cmd(
    client_id: str,
    redirect_port: int,
    timeout: int,
    as_json: bool,
) -> None:
    """Re-run the loopback authorize flow for an existing client."""
    crypto = _crypto()
    row = vault.get_client_with_secrets(vault._store_path(), client_id, crypto)
    if row is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    try:
        provider = get_provider(row["provider"])
    except UnknownProviderError as exc:
        click.echo(f"  ✗ Stored provider not in registry: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc

    scope_list = list(row.get("requested_scopes") or provider.default_scopes)

    _, granted_scopes, tokens = _run_authorize_dance(
        provider,
        client_id_value=row["client_id"],
        client_secret_value=row["client_secret"],
        scopes=scope_list,
        redirect_port=redirect_port,
        timeout=timeout,
    )

    updated = vault.update_tokens(
        vault._store_path(),
        crypto,
        client_id,
        tokens=tokens,
        granted_scopes=granted_scopes,
        status="authorized",
    )
    if as_json:
        click.echo(json.dumps(updated, indent=2, default=str))
        return
    click.echo(
        f"\n  ✓ Re-authorized {updated['display_name']!r} (id={updated['id']!r})"
    )


# refresh -----------------------------------------------------------


@oauth.command("refresh")
@click.argument("client_id")
@click.option("--json", "as_json", is_flag=True)
def refresh_cmd(client_id: str, as_json: bool) -> None:
    """Force a refresh against the vendor token endpoint."""
    crypto = _crypto()
    row = vault.get_client_with_secrets(vault._store_path(), client_id, crypto)
    if row is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    tokens = row.get("tokens") or {}
    refresh_token_value = tokens.get("refresh_token")
    if not refresh_token_value:
        click.echo(
            "  ✗ No refresh_token stored; use `oauth reauthorize` instead.",
            err=True,
        )
        raise click.exceptions.Exit(3)
    try:
        provider = get_provider(row["provider"])
    except UnknownProviderError as exc:
        click.echo(f"  ✗ Stored provider not in registry: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc

    try:
        response = asyncio.run(
            refresh_access_token(
                provider,
                client_id=row["client_id"],
                client_secret=row["client_secret"],
                refresh_token=refresh_token_value,
            )
        )
    except OAuthRefreshError as exc:
        vault.update_status(
            vault._store_path(),
            client_id,
            "expired",
            last_error={
                "code": exc.code,
                "message": str(exc),
                "at": _now_iso(),
            },
        )
        click.echo(f"  ✗ Refresh failed: {exc}", err=True)
        raise click.exceptions.Exit(3) from exc

    new_access = response.get("access_token") or tokens.get("access_token")
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
    updated = vault.update_tokens(
        vault._store_path(),
        crypto,
        client_id,
        tokens=new_tokens,
        granted_scopes=granted_scopes,
        status="authorized",
    )
    if as_json:
        click.echo(json.dumps(updated, indent=2, default=str))
        return
    click.echo(
        f"  ✓ Refreshed {updated['display_name']!r}. "
        f"expires_at: {new_tokens.get('expires_at')}"
    )


# revoke ------------------------------------------------------------


@oauth.command("revoke")
@click.argument("client_id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--json", "as_json", is_flag=True)
def revoke_cmd(client_id: str, yes: bool, as_json: bool) -> None:
    """Revoke vendor token (if supported) and clear local tokens."""
    crypto = _crypto()
    row = vault.get_client_with_secrets(vault._store_path(), client_id, crypto)
    if row is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    if not yes:
        click.confirm(
            f"Revoke OAuth client {row['display_name']!r} ({client_id})?",
            abort=True,
        )
    try:
        provider = get_provider(row["provider"])
    except UnknownProviderError as exc:
        click.echo(f"  ✗ Stored provider not in registry: {exc}", err=True)
        raise click.exceptions.Exit(2) from exc

    tokens = row.get("tokens") or {}
    access_token = tokens.get("access_token")

    # Best-effort vendor revoke; swallow failures so the local clear still happens.
    if provider.revoke_url and access_token:
        try:

            async def _do_revoke() -> None:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    await http.post(
                        provider.revoke_url,
                        data={
                            "token": access_token,
                            "token_type_hint": "access_token",
                        },
                    )

            asyncio.run(_do_revoke())
        except Exception as exc:  # noqa: BLE001
            click.echo(
                f"  ⚠ Vendor revoke endpoint failed ({exc!s}); "
                "clearing local tokens anyway.",
                err=True,
            )

    updated = vault.clear_tokens(vault._store_path(), client_id)
    if as_json:
        click.echo(json.dumps(updated, indent=2, default=str))
        return
    click.echo(f"  ✓ Revoked. Local tokens cleared.")


# delete ------------------------------------------------------------


@oauth.command("delete")
@click.argument("client_id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def delete_cmd(client_id: str, yes: bool) -> None:
    """Hard-delete an OAuth client record."""
    record = vault.get_client(vault._store_path(), client_id)
    if record is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    if not yes:
        click.confirm(
            f"Delete OAuth client {record['display_name']!r} ({client_id})? "
            "This is permanent.",
            abort=True,
        )
    ok = vault.delete_client(vault._store_path(), client_id)
    if not ok:
        click.echo(f"  ✗ Delete failed", err=True)
        raise click.exceptions.Exit(3)
    click.echo(f"  ✓ Deleted {client_id}")


# set-default -------------------------------------------------------


@oauth.command("set-default")
@click.argument("client_id")
@click.option("--json", "as_json", is_flag=True)
def set_default_cmd(client_id: str, as_json: bool) -> None:
    """Mark a client as default for its (project, provider) pair."""
    record = vault.get_client(vault._store_path(), client_id)
    if record is None:
        click.echo(f"  ✗ OAuth client {client_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    updated = vault.set_default(vault._store_path(), client_id)
    if as_json:
        click.echo(json.dumps(updated, indent=2, default=str))
        return
    click.echo(
        f"  ✓ {updated['display_name']!r} is now default for "
        f"({updated['project_id']}, {updated['provider']})"
    )


__all__ = ["oauth"]
