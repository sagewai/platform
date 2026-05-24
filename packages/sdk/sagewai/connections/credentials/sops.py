# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Mozilla SOPS credentials backend.

The connection record stores ``{"$sops": {"file": "...", "key": "..."}}``
markers in place of sensitive leaves. ``decrypt_fields`` shells out to
``sops --decrypt <file>``, parses the YAML output, and extracts the
referenced key. The platform does NOT write the SOPS file — operators
manage encryption manually (``sops edit <file>``) and just point the
connection record at the right ``(file, key)`` tuple.

Path safety: the SOPS file path is resolved against
``SAGEWAI_SOPS_ROOT`` (default ``~/.sagewai/sops/``). Any path that
resolves outside the root after symlink resolution raises
:class:`SopsDecryptError` immediately.

Caching: per-(file, mtime) in-process cache of decrypted YAML
contents. One subprocess call per file-decryption; multiple sensitive
fields from the same file in one request share the cached output.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import yaml

from sagewai.connections.credentials.base import _get_path, _set_path
from sagewai.connections.credentials.errors import (
    InvalidBackendConfigError,
    SopsDecryptError,
)
from sagewai.connections.models import HealthResult


def _sops_root() -> Path:
    override = os.environ.get("SAGEWAI_SOPS_ROOT")
    if override:
        return Path(override).expanduser()
    return (Path.home() / ".sagewai" / "sops").resolve()


def _resolve_sops_file(file_str: str) -> Path:
    """Resolve ``file_str`` against SAGEWAI_SOPS_ROOT; raise if outside."""
    root = _sops_root().resolve()
    candidate = (root / file_str).resolve()
    # If `root` doesn't exist yet, .resolve() is best-effort; we still want
    # the in-root check to work. Compare prefixes via os.path.commonpath:
    try:
        common = os.path.commonpath([str(root), str(candidate)])
    except ValueError:
        # commonpath raises on incomparable paths (different drives on
        # Windows, etc.). Treat as outside.
        raise SopsDecryptError(
            f"sops file path {file_str!r} resolves outside SAGEWAI_SOPS_ROOT={root}"
        )
    if common != str(root):
        raise SopsDecryptError(
            f"sops file path {file_str!r} resolves outside SAGEWAI_SOPS_ROOT={root}"
        )
    return candidate


# Per-process cache: {(file_path, mtime): parsed_yaml_dict}
_DECRYPT_CACHE: dict[tuple[str, float], dict[str, Any]] = {}


def _decrypt_file(path: Path) -> dict[str, Any]:
    """Shell out to ``sops --decrypt <path>``; parse YAML; cache by mtime."""
    if not path.exists():
        raise SopsDecryptError(f"sops file not found: {path}")
    mtime = path.stat().st_mtime
    cache_key = (str(path), mtime)
    if cache_key in _DECRYPT_CACHE:
        return _DECRYPT_CACHE[cache_key]
    try:
        result = subprocess.run(
            ["sops", "--decrypt", str(path)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SopsDecryptError(f"sops binary not on PATH: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise SopsDecryptError(
            f"sops failed to decrypt {path}: {stderr or 'returncode=' + str(result.returncode)}"
        )
    try:
        parsed = yaml.safe_load(result.stdout) or {}
    except yaml.YAMLError as exc:
        raise SopsDecryptError(f"sops output is not valid YAML: {exc}") from exc
    _DECRYPT_CACHE[cache_key] = parsed
    return parsed


def _extract(parsed: dict[str, Any], dotted_key: str) -> Any:
    """Walk a dotted-key path inside a parsed YAML dict."""
    cur: Any = parsed
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class SopsBackend:
    """Mozilla SOPS via subprocess. Operator-managed encrypted files."""

    id: ClassVar[str] = "sops"
    display_name: ClassVar[str] = "Mozilla SOPS"

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        # We store the marker; we do NOT write to the SOPS file.
        file_str = backend_config["file"]
        # The simple case: one key for the whole record. For per-field
        # remapping, an operator can edit backend_config later.
        out = protocol_data
        for path in sensitive_field_paths:
            leaf = _get_path(out, path)
            if leaf is None:
                continue
            marker = {"$sops": {"file": file_str, "key": backend_config["key"]}}
            out = _set_path(out, path, marker)
        return out

    def decrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        out = protocol_data
        for path in sensitive_field_paths:
            leaf = _get_path(out, path)
            if not isinstance(leaf, dict) or "$sops" not in leaf:
                continue
            sops_ref = leaf["$sops"]
            file_str = sops_ref.get("file") or backend_config["file"]
            key = sops_ref.get("key") or backend_config["key"]
            resolved = _resolve_sops_file(file_str)
            parsed = _decrypt_file(resolved)
            value = _extract(parsed, key)
            if value is None:
                raise SopsDecryptError(
                    f"sops file {file_str} has no value at key {key!r}"
                )
            out = _set_path(out, path, value)
        return out

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        self.validate_config(backend_config)
        if shutil.which("sops") is None:
            return HealthResult(ok=False, message="sops binary not on PATH")
        try:
            resolved = _resolve_sops_file(backend_config["file"])
        except SopsDecryptError as exc:
            return HealthResult(ok=False, message=str(exc))
        if not resolved.exists():
            return HealthResult(ok=False, message=f"sops file not found: {resolved}")
        return HealthResult(ok=True, message=f"sops ready ({resolved})")

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        if "file" not in backend_config or not isinstance(backend_config["file"], str):
            raise InvalidBackendConfigError(
                "sops backend requires 'file' (str) in backend_config"
            )
        if "key" not in backend_config or not isinstance(backend_config["key"], str):
            raise InvalidBackendConfigError(
                "sops backend requires 'key' (str) in backend_config"
            )
        if "key_path" in backend_config and not isinstance(backend_config["key_path"], str):
            raise InvalidBackendConfigError(
                "sops backend: 'key_path' must be str if present"
            )


__all__ = ["SopsBackend"]
