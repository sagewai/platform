# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Vault helpers for ``kind: "oauth_client"`` records.

OAuth-client records live alongside ``kind: "inference"`` and
``kind: "tool"`` records in the existing connections vault file at
``~/.sagewai/inference-providers.json`` (see
``sagewai.admin.connections_routes`` for the inference/tool surfaces).

Each record stores its long-lived ``client_secret`` and (once issued)
its ``tokens.access_token`` / ``tokens.refresh_token`` Sealed-encrypted
at the field level via :class:`sagewai.sealed.crypto.Crypto`. All other
fields are plaintext.

Public functions take ``store_path`` and (where decryption is needed)
``crypto`` as explicit arguments — the tests inject ``tmp_path`` and a
throwaway :class:`~cryptography.fernet.Fernet` key. The module-level
``_store_path()`` helper mirrors
``sagewai.admin.connections_routes._store_path()`` and is used only by
production code that doesn't already have a path injected.

Masking convention (functions that return a "masked record"): omit
``client_secret`` from the dict; if ``tokens`` is non-null, omit
``tokens.access_token`` and ``tokens.refresh_token``. Everything else is
returned verbatim.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sagewai.sealed.crypto import Crypto

_DEFAULT_STORE_PATH = Path.home() / ".sagewai" / "inference-providers.json"


def _store_path() -> Path:
    """Resolve the on-disk path. Honours ``SAGEWAI_ADMIN_STATE_FILE`` so
    test harnesses can sandbox the credential vault alongside the main
    admin-state file. Mirrors
    ``sagewai.admin.connections_routes._store_path()``.
    """
    state_env = os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
    if state_env:
        return Path(state_env).parent / "inference-providers.json"
    return _DEFAULT_STORE_PATH


def _read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "providers": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "providers": []}


def _write_store(path: Path, store: dict[str, Any]) -> None:
    """Atomic write — tempfile + ``os.replace`` + 0o600 perms.

    Pattern lifted verbatim from
    ``sagewai.admin.connections_routes._write_store()`` so the two
    surfaces share crash semantics on the shared vault file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=path.parent, prefix=".inference-providers.", suffix=".tmp",
    ) as tmp:
        json.dump(store, tmp, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(provider: str) -> str:
    return f"oauth_{provider}_{secrets.token_hex(8)}"


def _is_oauth_row(row: dict[str, Any]) -> bool:
    return row.get("kind") == "oauth_client"


def _find_oauth_row(
    store: dict[str, Any], client_id: str,
) -> tuple[int | None, dict[str, Any] | None]:
    rows = store.get("providers") or []
    for i, row in enumerate(rows):
        if _is_oauth_row(row) and row.get("id") == client_id:
            return i, row
    return None, None


def _mask(row: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with secrets stripped.

    Strips ``client_secret`` and (if ``tokens`` is non-null)
    ``tokens.access_token`` / ``tokens.refresh_token``. Everything else
    passes through.
    """
    masked = deepcopy(row)
    masked.pop("client_secret", None)
    tokens = masked.get("tokens")
    if isinstance(tokens, dict):
        tokens.pop("access_token", None)
        tokens.pop("refresh_token", None)
    return masked


def list_clients(
    store_path: Path, project_id: str | None,
) -> list[dict[str, Any]]:
    """List masked oauth_client records for the project."""
    store = _read_store(store_path)
    out: list[dict[str, Any]] = []
    for row in store.get("providers") or []:
        if not _is_oauth_row(row):
            continue
        if row.get("project_id") != project_id:
            continue
        out.append(_mask(row))
    return out


def get_client(
    store_path: Path, client_id: str,
) -> dict[str, Any] | None:
    """Masked single record by id, or None."""
    store = _read_store(store_path)
    _, row = _find_oauth_row(store, client_id)
    if row is None:
        return None
    return _mask(row)


def get_client_with_secrets(
    store_path: Path, client_id: str, crypto: Crypto,
) -> dict[str, Any] | None:
    """Decrypted single record by id, or None.

    Internal use only — used by the executor and refresh paths. Never
    returned over the admin API.
    """
    store = _read_store(store_path)
    _, row = _find_oauth_row(store, client_id)
    if row is None:
        return None
    decrypted = deepcopy(row)
    decrypted["client_secret"] = crypto.decrypt(row["client_secret"])
    tokens = row.get("tokens")
    if isinstance(tokens, dict):
        dec_tokens = deepcopy(tokens)
        dec_tokens["access_token"] = crypto.decrypt(tokens["access_token"])
        dec_tokens["refresh_token"] = crypto.decrypt(tokens["refresh_token"])
        decrypted["tokens"] = dec_tokens
    return decrypted


def create_client(
    store_path: Path,
    crypto: Crypto,
    *,
    provider: str,
    project_id: str | None,
    display_name: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    requested_scopes: list[str],
) -> dict[str, Any]:
    """Create a new client record (``status='pending'``, no tokens).

    Returns the masked record. The first client for a given
    ``(project_id, provider)`` becomes the default; subsequent clients
    are created with ``is_default=False`` so an existing default isn't
    silently clobbered.
    """
    store = _read_store(store_path)
    rows = store.setdefault("providers", [])

    existing_for_pair = [
        r for r in rows
        if _is_oauth_row(r)
        and r.get("project_id") == project_id
        and r.get("provider") == provider
    ]
    is_default = len(existing_for_pair) == 0

    now = _now_iso()
    new_id = _generate_id(provider)
    row: dict[str, Any] = {
        "id": new_id,
        "kind": "oauth_client",
        "project_id": project_id,
        "provider": provider,
        "display_name": display_name,
        "client_id": client_id,
        "client_secret": crypto.encrypt(client_secret),
        "redirect_uri": redirect_uri,
        "requested_scopes": list(requested_scopes),
        "granted_scopes": [],
        "tokens": None,
        "is_default": is_default,
        "status": "pending",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    rows.append(row)
    _write_store(store_path, store)
    return _mask(row)


def update_tokens(
    store_path: Path,
    crypto: Crypto,
    client_id: str,
    *,
    tokens: dict[str, Any],
    granted_scopes: list[str],
    status: str = "authorized",
) -> dict[str, Any]:
    """Persist new tokens after exchange or refresh. Returns masked record.

    ``granted_scopes`` is sorted lexicographically before storing so
    callers don't have to remember to do it. ``tokens.access_token`` and
    ``tokens.refresh_token`` are encrypted; other fields pass through
    unchanged.
    """
    store = _read_store(store_path)
    idx, row = _find_oauth_row(store, client_id)
    if row is None or idx is None:
        raise KeyError(f"oauth_client {client_id!r} not found")

    encrypted_tokens = dict(tokens)
    encrypted_tokens["access_token"] = crypto.encrypt(tokens["access_token"])
    encrypted_tokens["refresh_token"] = crypto.encrypt(tokens["refresh_token"])

    row["tokens"] = encrypted_tokens
    row["granted_scopes"] = sorted(granted_scopes)
    row["status"] = status
    row["last_error"] = None
    row["updated_at"] = _now_iso()

    store["providers"][idx] = row
    _write_store(store_path, store)
    return _mask(row)


def update_status(
    store_path: Path,
    client_id: str,
    status: str,
    *,
    last_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update status only (e.g., 'expired', 'revoked'). Returns masked record."""
    store = _read_store(store_path)
    idx, row = _find_oauth_row(store, client_id)
    if row is None or idx is None:
        raise KeyError(f"oauth_client {client_id!r} not found")

    row["status"] = status
    row["last_error"] = last_error
    row["updated_at"] = _now_iso()

    store["providers"][idx] = row
    _write_store(store_path, store)
    return _mask(row)


def set_default(store_path: Path, client_id: str) -> dict[str, Any]:
    """Mark this client as default for ``(project_id, provider)``;
    unset any prior default for the same pair in the same transaction.
    """
    store = _read_store(store_path)
    idx, row = _find_oauth_row(store, client_id)
    if row is None or idx is None:
        raise KeyError(f"oauth_client {client_id!r} not found")

    target_project = row.get("project_id")
    target_provider = row.get("provider")
    now = _now_iso()

    for other in store.get("providers") or []:
        if not _is_oauth_row(other):
            continue
        if other.get("project_id") != target_project:
            continue
        if other.get("provider") != target_provider:
            continue
        if other.get("id") == client_id:
            if not other.get("is_default"):
                other["is_default"] = True
                other["updated_at"] = now
        else:
            if other.get("is_default"):
                other["is_default"] = False
                other["updated_at"] = now

    _write_store(store_path, store)
    # Re-find to get the post-write copy.
    _, refreshed = _find_oauth_row(store, client_id)
    assert refreshed is not None  # we just confirmed it exists
    return _mask(refreshed)


def find_default_client(
    store_path: Path,
    crypto: Crypto,
    project_id: str | None,
    provider: str,
) -> dict[str, Any] | None:
    """Decrypted default client for ``(project_id, provider)`` or None.

    Used by the executor to resolve which oauth_client supplies tokens
    for a given tool invocation.
    """
    store = _read_store(store_path)
    for row in store.get("providers") or []:
        if not _is_oauth_row(row):
            continue
        if row.get("project_id") != project_id:
            continue
        if row.get("provider") != provider:
            continue
        if not row.get("is_default"):
            continue
        # Decrypt via the same path as get_client_with_secrets.
        return get_client_with_secrets(store_path, row["id"], crypto)
    return None


def clear_tokens(store_path: Path, client_id: str) -> dict[str, Any]:
    """Wipe ``tokens`` to ``None``, set ``status='revoked'``, return masked record.

    Used by the revoke admin route after notifying the vendor (if the
    provider exposes a revoke endpoint). Distinct from ``update_status``
    because that helper preserves the encrypted token blob — for revoke
    we want the stored access/refresh tokens gone from disk.
    """
    store = _read_store(store_path)
    idx, row = _find_oauth_row(store, client_id)
    if row is None or idx is None:
        raise KeyError(f"oauth_client {client_id!r} not found")

    row["tokens"] = None
    row["granted_scopes"] = []
    row["status"] = "revoked"
    row["last_error"] = None
    row["updated_at"] = _now_iso()

    store["providers"][idx] = row
    _write_store(store_path, store)
    return _mask(row)


def delete_client(store_path: Path, client_id: str) -> bool:
    """Hard delete by id; returns True if deleted."""
    store = _read_store(store_path)
    idx, row = _find_oauth_row(store, client_id)
    if row is None or idx is None:
        return False
    store["providers"].pop(idx)
    _write_store(store_path, store)
    return True
