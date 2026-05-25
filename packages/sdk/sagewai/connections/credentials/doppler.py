# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Doppler credentials backend.

Stores ``{"$doppler": {"name": "<NAME>"}}`` markers in place of
sensitive leaves. Decryption reads from Doppler's REST API via the
existing ``httpx`` dependency (no new SDK). Per-call HTTP client; bulk-
read on first decrypt so one round-trip serves multiple sensitive
fields.

Read-only: the platform never writes to Doppler. Operators populate
secrets via the Doppler UI / CLI / Terraform.

Service tokens are scoped to one ``(project, config)`` pair. To switch
projects, register a new connection with a fresh token.
"""
from __future__ import annotations

from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sagewai.connections.credentials.base import _get_path, _set_path
from sagewai.connections.credentials.errors import (
    DopplerApiError,
    DopplerAuthError,
    DopplerConfigError,
)
from sagewai.connections.models import HealthResult


class DopplerBackendConfig(BaseModel):
    """Per-connection Doppler backend configuration."""

    model_config = ConfigDict(extra="forbid")
    service_token: str = Field(..., min_length=10, pattern=r"^dp\.st\.")
    project: str = Field(..., min_length=1)
    config: str = Field(..., min_length=1)
    name_prefix: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]*$")
    base_url: str = "https://api.doppler.com"


def _field_path_to_name(path_str: str, name_prefix: str) -> str:
    """Derive a Doppler secret name from a sensitive-field path.

    "tokens.access_token" + "SPOTIFY_MARKETING"
        -> "SPOTIFY_MARKETING_TOKENS_ACCESS_TOKEN"
    """
    upper = path_str.replace(".", "_").upper()
    return f"{name_prefix}_{upper}"


class DopplerBackend:
    """Doppler SaaS credentials backend (HTTP API)."""

    id: ClassVar[str] = "doppler"
    display_name: ClassVar[str] = "Doppler"

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        try:
            DopplerBackendConfig.model_validate(backend_config)
        except ValidationError as exc:
            raise DopplerConfigError(
                f"doppler backend config invalid: {exc}"
            ) from exc

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        cfg = DopplerBackendConfig.model_validate(backend_config)
        out = protocol_data
        for path_str in sensitive_field_paths:
            leaf = _get_path(out, path_str)
            if leaf is None:
                continue
            if isinstance(leaf, dict) and "$doppler" in leaf:
                continue
            name = _field_path_to_name(path_str, cfg.name_prefix)
            out = _set_path(out, path_str, {"$doppler": {"name": name}})
        return out

    def decrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        cfg = DopplerBackendConfig.model_validate(backend_config)

        # Skip HTTP entirely if no markers are present.
        markers_to_resolve: list[tuple[str, str]] = []
        for path_str in sensitive_field_paths:
            leaf = _get_path(protocol_data, path_str)
            if isinstance(leaf, dict) and "$doppler" in leaf:
                markers_to_resolve.append((path_str, leaf["$doppler"]["name"]))
        if not markers_to_resolve:
            return protocol_data

        # Bulk-read all secrets for this (project, config) in ONE call.
        params = {"project": cfg.project, "config": cfg.config}
        headers = {"Authorization": f"Bearer {cfg.service_token}"}
        try:
            with httpx.Client(base_url=cfg.base_url, timeout=10.0) as client:
                resp = client.get(
                    "/v3/configs/config/secrets",
                    params=params, headers=headers,
                )
        except httpx.HTTPError as exc:
            raise DopplerApiError(f"doppler network error: {exc}") from exc

        if resp.status_code == 401:
            raise DopplerAuthError(
                f"doppler service_token rejected (401): {resp.text}"
            )
        if resp.status_code >= 400:
            raise DopplerApiError(
                f"doppler API error {resp.status_code}: {resp.text}"
            )

        body = resp.json()
        secrets_map: dict[str, dict] = body.get("secrets", {})

        # Apply markers
        out = protocol_data
        for path_str, name in markers_to_resolve:
            secret_entry = secrets_map.get(name)
            if secret_entry is None:
                raise DopplerApiError(
                    f"doppler secret {name!r} not found in "
                    f"project={cfg.project!r} config={cfg.config!r}"
                )
            # Prefer computed (resolves {{secrets.OTHER}} refs); fall back to raw
            value = secret_entry.get("computed")
            if value is None:
                value = secret_entry.get("raw", "")
            out = _set_path(out, path_str, value)
        return out

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        self.validate_config(backend_config)
        cfg = DopplerBackendConfig.model_validate(backend_config)
        params = {"project": cfg.project, "config": cfg.config}
        headers = {"Authorization": f"Bearer {cfg.service_token}"}
        try:
            with httpx.Client(base_url=cfg.base_url, timeout=10.0) as client:
                resp = client.get(
                    "/v3/configs/config", params=params, headers=headers,
                )
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, message=f"doppler network error: {exc}")
        if resp.status_code == 200:
            return HealthResult(ok=True, message="doppler reachable")
        if resp.status_code == 401:
            return HealthResult(ok=False, message="invalid service_token (401)")
        if resp.status_code == 404:
            return HealthResult(
                ok=False,
                message=f"project or config not found (404): {cfg.project}/{cfg.config}",
            )
        return HealthResult(
            ok=False, message=f"doppler API error {resp.status_code}: {resp.text}",
        )


__all__ = [
    "DopplerBackend",
    "DopplerBackendConfig",
]
