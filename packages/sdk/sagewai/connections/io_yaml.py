# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""YAML export + import for connection records.

This module hosts both directions: ``export_to_yaml(...)`` and
``import_from_yaml(...)``. They share the format constants, the
sensitive-path walking logic, and the placeholder substitution code,
so they live together.

The wire format is documented at
``apps/docs/app/docs/connections/import-export/page.mdx``.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

from sagewai.connections.credentials.router import CredentialsBackendRouter
from sagewai.connections.io_errors import (
    ImportDefaultCollisionError,
    ImportDisplayNameCollisionError,
    ImportEnvVarMissingError,
    ImportIdCollisionError,
    ImportMasterKeyMismatchError,
    ImportProtocolDataInvalidError,
    ImportUnknownBackendError,
    ImportUnknownProtocolError,
    ImportUnknownVersionError,
    ImportYamlParseError,
)
from sagewai.connections.io_errors import ImportError as _ImportError
from sagewai.connections.protocols import all_protocols, get_protocol
from sagewai.connections.protocols.base import get_sensitive_field_paths_for
from sagewai.connections.store import ConnectionStore

# ── format constants ──────────────────────────────────────────────────


EXPORT_FORMAT_VERSION = 1
EXPORTED_BY_VERSION = "1.0.0"  # bumped when the export format changes meaningfully

SecretsMode = Literal["redacted", "encrypted", "placeholder"]
ImportMode = Literal["create-only", "upsert", "skip-existing"]

_SECRETS_MODES = ("redacted", "encrypted", "placeholder")
_IMPORT_MODES = ("create-only", "upsert", "skip-existing")


# ── placeholder substitution ──────────────────────────────────────────


# Strict identifier regex — uppercase only. Matches ${IDENTIFIER}.
# Doesn't match ${lower} or ${mixed} (those pass through as literals).
_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def _to_upper_snake(s: str) -> str:
    """Convert a kebab-case or snake_case string to UPPER_SNAKE_CASE."""
    return re.sub(r"[^A-Z0-9_]", "_", s.upper().replace("-", "_"))


def _placeholder_for(display_name: str, field_path: str) -> str:
    """Compose the canonical ${VAR} placeholder for a sensitive field."""
    # field_path is dotted (e.g., "client_secret" or "tokens.access_token").
    leaf = field_path.split(".")[-1]
    return f"${{{_to_upper_snake(display_name)}_{_to_upper_snake(leaf)}}}"


def _set_path_in_pd(pd: dict, path: str, value: Any) -> None:
    """Set ``pd[a][b][c] = value`` for path 'a.b.c' (mutates in place).

    Mirrors the path-walking behavior of CredentialsBackendRouter.encrypt:
    sensitive field paths are dotted into protocol_data directly.
    Skips silently if any intermediate key is missing.
    """
    parts = path.split(".")
    cur: Any = pd
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict) and parts[-1] in cur:
        cur[parts[-1]] = value


def _get_path_in_pd(pd: dict, path: str) -> Any:
    """Get ``pd[a][b][c]`` for path 'a.b.c'. Returns None if any segment missing."""
    parts = path.split(".")
    cur: Any = pd
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# Storage-form markers used by the credentials backends. A sensitive field
# carrying any of these (as either a prefixed string or a marker dict) is in
# ciphertext form and requires the matching backend's master key / access to
# decrypt. Used at import-time to gate sample-decrypt detection AND at
# export-time to refuse to emit plaintext under ``secrets_mode: encrypted``.
_STORAGE_FORM_PREFIXES = ("fernet:",)
_STORAGE_FORM_DICT_KEYS = ("$env", "$sops", "$vault", "$doppler")


def _looks_like_storage_marker(value: Any) -> bool:
    """True if ``value`` is in one of the known credentials-backend storage forms.

    Recognized forms:
    - ``fernet:`` prefix (LocalBackend ciphertext)
    - ``{"$env": ...}`` / ``{"$sops": ...}`` / ``{"$vault": ...}`` / ``{"$doppler": ...}``
      (Env / SOPS / Vault / Doppler markers)

    Plain strings, ``None``, lists, and other dict shapes return False.
    """
    if isinstance(value, str):
        return value.startswith(_STORAGE_FORM_PREFIXES)
    if isinstance(value, dict):
        return any(k in value for k in _STORAGE_FORM_DICT_KEYS)
    return False


def _has_storage_form_markers(pd: dict, sensitive_paths: tuple[str, ...]) -> bool:
    """Return True if any sensitive path holds a ciphertext-style marker."""
    for path in sensitive_paths:
        if _looks_like_storage_marker(_get_path_in_pd(pd, path)):
            return True
    return False


# ── export ────────────────────────────────────────────────────────────


def export_to_yaml(
    *,
    store: ConnectionStore,
    router: CredentialsBackendRouter,
    project_id: str | None,
    secrets_mode: SecretsMode = "redacted",
    protocols: tuple[str, ...] | None = None,
    tags: tuple[str, ...] | None = None,
    include_id: bool = False,
) -> str:
    """Export connections in a project to a YAML document.

    Args:
        store: The ConnectionStore to read from.
        router: The CredentialsBackendRouter (needed for encrypted-mode
            sample decrypts and for sensitive-field path resolution).
        project_id: Project to export.
        secrets_mode: 'redacted' (default), 'encrypted', or 'placeholder'.
        protocols: Optional filter — only export connections matching one
            of these protocols. None or empty tuple = no protocol filter.
        tags: Optional filter — only export connections with at least one
            of these tags. None or empty tuple = no tag filter.
        include_id: If True, include the connection's internal id in the
            output. Default False (id is regenerated on import).

    Returns:
        The YAML document as a string.
    """
    if secrets_mode not in _SECRETS_MODES:
        raise ValueError(
            f"unknown secrets_mode {secrets_mode!r}; expected one of {_SECRETS_MODES}"
        )

    rows = list(store.list(project_id=project_id))
    if protocols:
        rows = [r for r in rows if r.protocol in protocols]
    if tags:
        tag_set = set(tags)
        rows = [r for r in rows if tag_set.intersection(r.tags)]

    exported_connections: list[dict[str, Any]] = []
    for conn in rows:
        plugin = get_protocol(conn.protocol)
        sensitive_paths = get_sensitive_field_paths_for(plugin, conn)
        # Deep-copy so we never mutate the store's record.
        pd = copy.deepcopy(dict(conn.protocol_data))

        # Apply secrets mode to sensitive paths.
        for path in sensitive_paths:
            current = _get_path_in_pd(pd, path)
            if secrets_mode == "redacted":
                _set_path_in_pd(pd, path, None)
            elif secrets_mode == "encrypted":
                # Keep the storage-form marker (fernet:, $env, $sops,
                # $vault, $doppler). If the field is already null, leave
                # it alone. Otherwise — plaintext string, raw dict, list,
                # unexpected shape — redact for safety. The encrypted-mode
                # promise is "ciphertext travels", not "platform encrypts
                # on export"; emitting plaintext under this mode would
                # silently leak a secret operators believe is protected.
                if current is None:
                    continue  # already null; nothing to do
                if _looks_like_storage_marker(current):
                    continue  # ciphertext / marker — travels as-is
                _set_path_in_pd(pd, path, None)
                logger.warning(
                    "export_to_yaml: redacting plaintext sensitive value at "
                    "%s on connection %s (encrypted mode requires "
                    "storage-form markers; plaintext would leak)",
                    path,
                    conn.display_name,
                )
            elif secrets_mode == "placeholder":
                _set_path_in_pd(
                    pd,
                    path,
                    _placeholder_for(conn.display_name, path),
                )

        # Build the row dict — controlled field order for human readability.
        row: dict[str, Any] = {}
        if include_id:
            row["id"] = conn.id
        row["protocol"] = conn.protocol
        row["display_name"] = conn.display_name
        row["tags"] = list(conn.tags)
        row["credentials_backend"] = conn.credentials_backend
        row["is_default"] = conn.is_default
        row["protocol_data"] = pd
        exported_connections.append(row)

    document: dict[str, Any] = {
        "version": EXPORT_FORMAT_VERSION,
        "project": {"id": project_id, "display_name": project_id},
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by_version": EXPORTED_BY_VERSION,
        "secrets_mode": secrets_mode,
        "connections": exported_connections,
    }

    return yaml.safe_dump(
        document,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


# ── import ────────────────────────────────────────────────────────────


def _err(
    row_index: int,
    protocol: str,
    display_name: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Build an error entry for the result dict."""
    return {
        "row_index": row_index,
        "protocol": protocol,
        "display_name": display_name,
        "code": code,
        "message": message,
    }


def _resolve_placeholders(
    pd: dict,
    sensitive_paths: tuple[str, ...],
    display_name: str,
    errors_out: list,
    row_index: int,
    protocol: str,
) -> dict:
    """Walk sensitive paths and replace any `${VAR}` values with the env var.

    Returns the mutated protocol_data dict. Appends errors to errors_out.
    """
    for path in sensitive_paths:
        current = _get_path_in_pd(pd, path)
        if not isinstance(current, str):
            continue
        match = _PLACEHOLDER_RE.match(current)
        if not match:
            continue
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            errors_out.append(
                _err(
                    row_index,
                    protocol,
                    display_name,
                    ImportEnvVarMissingError.code,
                    f"environment variable {var_name!r} (referenced at {path}) is not set",
                )
            )
            # Leave the placeholder in place; the row will be skipped via the error.
            continue
        _set_path_in_pd(pd, path, value)
    return pd


def import_from_yaml(
    *,
    yaml_text: str,
    store: ConnectionStore,
    router: CredentialsBackendRouter,
    project_id: str | None,
    mode: ImportMode = "create-only",
    dry_run: bool = False,
    preserve_ids: bool = False,
) -> dict[str, Any]:
    """Import a YAML export document into a target ConnectionStore.

    Args:
        yaml_text: The YAML document to parse.
        store: The target ConnectionStore.
        router: The CredentialsBackendRouter (for sample-decrypt validation).
        project_id: Target project_id. Falls back to the YAML's project.id if None.
        mode: 'create-only' (default), 'upsert', or 'skip-existing'.
        dry_run: If True, validate + report but don't persist.
        preserve_ids: If True, honor the 'id' field in each YAML row.

    Returns:
        Dict with keys ``dry_run``, ``created``, ``updated``, ``skipped``,
        ``errors``. Each list entry is a dict with ``id``/``protocol``/``display_name``
        (for created/updated/skipped) or ``row_index``/``protocol``/``display_name``/``code``/``message``
        (for errors).
    """
    from sagewai.connections.credentials import all_backends
    from sagewai.connections.errors import (
        DuplicateDisplayNameError,
        IdCollisionError,
    )

    _backend_ids = {b.id for b in all_backends()}

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "created": [],
        "updated": [],
        "skipped": [],
        "errors": [],
    }

    # Step 1: parse YAML
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        line_obj = getattr(exc, "problem_mark", None)
        line = (line_obj.line + 1) if line_obj is not None else None
        result["errors"].append(
            _err(0, "", "", ImportYamlParseError.code, str(exc))
        )
        result["errors"][-1]["line"] = line
        return result

    if not isinstance(doc, dict):
        result["errors"].append(
            _err(
                0,
                "",
                "",
                ImportYamlParseError.code,
                "top-level must be a mapping",
            )
        )
        return result

    # Step 2: version check
    version = doc.get("version")
    if version != EXPORT_FORMAT_VERSION:
        result["errors"].append(
            _err(
                0,
                "",
                "",
                ImportUnknownVersionError.code,
                f"unsupported export format version (found {version!r}; only {EXPORT_FORMAT_VERSION} is supported)",
            )
        )
        return result

    # Step 3: pre-validate all rows. In create-only mode, ANY error → abort.
    connections = doc.get("connections", []) or []
    target_project_id = (
        project_id
        if project_id is not None
        else (doc.get("project", {}) or {}).get("id")
    )

    known_protocol_ids = {p.id for p in all_protocols()}
    known_backend_ids = _backend_ids

    # Pre-check for default-flag collisions within the batch (by protocol).
    # Collisions are surfaced as errors; rows in collided protocols are
    # still processed (so non-default fields land in upsert/skip-existing
    # mode), but the post-write set_default() promotion is suppressed for
    # them — last-write-wins would otherwise silently demote the existing
    # default and promote an arbitrary row.
    defaults_seen: dict[str, list[str]] = {}
    for row in connections:
        if isinstance(row, dict) and row.get("is_default"):
            key = row.get("protocol", "")
            defaults_seen.setdefault(key, []).append(row.get("display_name", ""))
    protocols_with_default_collision: set[str] = set()
    for prot, names in defaults_seen.items():
        if len(names) > 1:
            result["errors"].append(
                _err(
                    -1,
                    prot,
                    "",
                    ImportDefaultCollisionError.code,
                    f"{prot}: multiple is_default=true rows ({names!r})",
                )
            )
            protocols_with_default_collision.add(prot)

    # Process each row
    pending_writes: list[tuple[str, dict[str, Any]]] = []

    for idx, row in enumerate(connections):
        if not isinstance(row, dict):
            result["errors"].append(
                _err(
                    idx,
                    "",
                    "",
                    ImportYamlParseError.code,
                    f"row {idx} is not a mapping",
                )
            )
            continue
        protocol = row.get("protocol", "")
        display_name = row.get("display_name", "")

        # Unknown protocol
        if protocol not in known_protocol_ids:
            result["errors"].append(
                _err(
                    idx,
                    protocol,
                    display_name,
                    ImportUnknownProtocolError.code,
                    f"unknown protocol: {protocol!r}",
                )
            )
            continue

        plugin = get_protocol(protocol)

        # Unknown backend
        backend_dict = row.get("credentials_backend") or {}
        backend_kind = backend_dict.get("kind") if isinstance(backend_dict, dict) else None
        if backend_kind and backend_kind not in known_backend_ids:
            result["errors"].append(
                _err(
                    idx,
                    protocol,
                    display_name,
                    ImportUnknownBackendError.code,
                    f"unknown credentials backend: {backend_kind!r}",
                )
            )
            continue

        # Deep-copy so per-row mutation doesn't leak.
        pd = copy.deepcopy(dict(row.get("protocol_data") or {}))

        # Resolve placeholders. Build a minimal duck-typed Connection-like
        # object — MCP plugin's sensitive_field_paths_for uses
        # connection.protocol_data, so we expose the same attribute.
        tmp_conn = type(
            "Tmp",
            (),
            {
                "protocol": protocol,
                "display_name": display_name,
                "protocol_data": pd,
            },
        )()
        sensitive_paths = get_sensitive_field_paths_for(plugin, tmp_conn)

        # Encrypted-mode sample decrypt: if the source YAML declared
        # ``secrets_mode: encrypted`` AND this row carries any storage-form
        # marker on a sensitive field, try a sample decrypt under the
        # target's CredentialsBackendRouter BEFORE proceeding. A failure
        # here means the target environment doesn't share the source
        # master key (or lacks access to the configured backend), and
        # silently importing would yield records that fail at first use.
        if doc.get("secrets_mode") == "encrypted" and sensitive_paths and (
            _has_storage_form_markers(pd, sensitive_paths)
        ):
            try:
                router.decrypt(
                    copy.deepcopy(pd),
                    sensitive_field_paths=tuple(sensitive_paths),
                    connection_credentials_backend=row.get("credentials_backend"),
                )
            except Exception as exc:
                result["errors"].append(
                    _err(
                        idx,
                        protocol,
                        display_name,
                        ImportMasterKeyMismatchError.code,
                        f"sample decrypt failed: {exc}; target environment "
                        "doesn't share the source master key or backend access",
                    )
                )
                continue

        error_count_before = len(result["errors"])
        pd = _resolve_placeholders(
            pd, sensitive_paths, display_name, result["errors"], idx, protocol
        )
        if len(result["errors"]) > error_count_before:
            continue

        # Validate protocol_data via the plugin's schema. We do this on a
        # plaintext-resolved copy (placeholders already substituted). For
        # redacted-mode imports, null values may fail the schema — that's
        # expected behavior (operators must fill in the secrets before
        # importing a redacted-mode YAML).
        try:
            plugin.protocol_data_schema()(**pd)
        except Exception as exc:
            result["errors"].append(
                _err(
                    idx,
                    protocol,
                    display_name,
                    ImportProtocolDataInvalidError.code,
                    str(exc),
                )
            )
            continue

        # preserve_ids handling
        explicit_id = row.get("id")
        if preserve_ids and not explicit_id:
            result["errors"].append(
                _err(
                    idx,
                    protocol,
                    display_name,
                    ImportIdCollisionError.code,
                    "preserve_ids=true but no 'id' field in YAML row",
                )
            )
            continue

        # Collision check
        existing = next(
            (
                c
                for c in store.list(project_id=target_project_id)
                if c.protocol == protocol and c.display_name == display_name
            ),
            None,
        )

        if existing is not None:
            if mode == "create-only":
                result["errors"].append(
                    _err(
                        idx,
                        protocol,
                        display_name,
                        ImportDisplayNameCollisionError.code,
                        f"{protocol}:{display_name}: display_name already exists in target",
                    )
                )
                continue
            elif mode == "skip-existing":
                result["skipped"].append(
                    {
                        "id": existing.id,
                        "protocol": protocol,
                        "display_name": display_name,
                    }
                )
                continue
            elif mode == "upsert":
                pending_writes.append(
                    (
                        "update",
                        {
                            "existing_id": existing.id,
                            "row": row,
                            "pd": pd,
                            "idx": idx,
                        },
                    )
                )
                continue

        # Create path
        pending_writes.append(
            (
                "create",
                {
                    "row": row,
                    "pd": pd,
                    "idx": idx,
                    "explicit_id": explicit_id if preserve_ids else None,
                },
            )
        )

    # In create-only mode, any error → abort with zero writes
    if mode == "create-only" and result["errors"]:
        return result

    # If dry_run, populate the result without writing
    if dry_run:
        for action, w in pending_writes:
            entry = {
                "id": w.get("explicit_id") or w.get("existing_id") or "<would-be-generated>",
                "protocol": w["row"]["protocol"],
                "display_name": w["row"]["display_name"],
            }
            if action == "create":
                result["created"].append(entry)
            elif action == "update":
                result["updated"].append(entry)
        return result

    # Execute writes
    for action, w in pending_writes:
        row = w["row"]
        pd = w["pd"]
        target_id: str | None = None
        try:
            if action == "create":
                conn = store.create(
                    protocol=row["protocol"],
                    project_id=target_project_id,
                    display_name=row["display_name"],
                    tags=list(row.get("tags") or []),
                    protocol_data=pd,
                    credentials_backend=row.get("credentials_backend"),
                    id_override=w.get("explicit_id"),
                )
                target_id = conn.id
                result["created"].append(
                    {
                        "id": conn.id,
                        "protocol": conn.protocol,
                        "display_name": conn.display_name,
                    }
                )
            elif action == "update":
                updated = store.update(
                    w["existing_id"],
                    display_name=row["display_name"],
                    tags=list(row.get("tags") or []),
                    protocol_data=pd,
                    credentials_backend=row.get("credentials_backend"),
                )
                target_id = updated.id
                result["updated"].append(
                    {
                        "id": updated.id,
                        "protocol": updated.protocol,
                        "display_name": updated.display_name,
                    }
                )

            # Spec: ``upsert``: imported default wins, previously-default
            # record unflagged. The same intent applies on create — when
            # the YAML row carries ``is_default: true`` it should be
            # promoted regardless of whether ``store.create``'s auto-
            # default heuristic picked it. Promotion is best-effort: if
            # ``set_default`` fails the row still landed.
            # Skip promotion for rows in a protocol that hit an
            # is_default batch-level collision: last-write-wins would
            # otherwise silently demote the existing default and promote
            # an arbitrary row. Surface the collision via the error and
            # leave the default flag untouched.
            if (
                target_id is not None
                and row.get("is_default") is True
                and action in ("create", "update")
                and row["protocol"] not in protocols_with_default_collision
            ):
                try:
                    store.set_default(target_id)
                except Exception:  # pragma: no cover — non-fatal
                    pass
        except (DuplicateDisplayNameError, IdCollisionError) as exc:
            code = (
                ImportIdCollisionError.code
                if isinstance(exc, IdCollisionError)
                else ImportDisplayNameCollisionError.code
            )
            result["errors"].append(
                _err(
                    w["idx"],
                    row["protocol"],
                    row["display_name"],
                    code,
                    str(exc),
                )
            )
        except Exception as exc:
            result["errors"].append(
                _err(
                    w["idx"],
                    row["protocol"],
                    row["display_name"],
                    _ImportError.code,
                    str(exc),
                )
            )

    return result


__all__ = [
    "EXPORT_FORMAT_VERSION",
    "EXPORTED_BY_VERSION",
    "ImportMode",
    "SecretsMode",
    "export_to_yaml",
    "import_from_yaml",
]
