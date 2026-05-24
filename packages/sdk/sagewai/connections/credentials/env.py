# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Environment-variable credentials backend.

The connection record stores ``{"$env": "<VAR_NAME>"}`` markers in
place of sensitive leaves. ``decrypt_fields`` reads
``os.environ[<VAR_NAME>]`` at call time. The plaintext is NOT stored
in the connection record — operators pre-export the env vars (works
naturally with ``.env`` files via the existing dotenv ecosystem).
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

from sagewai.connections.credentials.base import _get_path, _set_path
from sagewai.connections.credentials.errors import (
    InvalidBackendConfigError,
    MissingEnvVarError,
)
from sagewai.connections.models import HealthResult


class EnvBackend:
    """Reads sensitive values from environment variables at call time."""

    id: ClassVar[str] = "env"
    display_name: ClassVar[str] = "Environment variables"

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        field_to_env = backend_config["field_to_env"]
        out = protocol_data
        for path in sensitive_field_paths:
            env_name = field_to_env.get(path)
            if env_name is None:
                continue  # path not mapped to an env var; pass through
            leaf = _get_path(out, path)
            if leaf is None:
                continue
            out = _set_path(out, path, {"$env": env_name})
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
            if not isinstance(leaf, dict) or "$env" not in leaf:
                continue  # not an env marker — pass through unchanged
            env_name = leaf["$env"]
            value = os.environ.get(env_name)
            if value is None:
                raise MissingEnvVarError(
                    f"env backend: {path!r} references {env_name!r} which is unset"
                )
            out = _set_path(out, path, value)
        return out

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        self.validate_config(backend_config)
        field_to_env = backend_config["field_to_env"]
        missing = sorted(
            env_name for env_name in field_to_env.values()
            if os.environ.get(env_name) is None
        )
        if missing:
            return HealthResult(ok=False, message=f"env vars unset: {missing!r}")
        return HealthResult(ok=True, message="all declared env vars set")

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        if "field_to_env" not in backend_config:
            raise InvalidBackendConfigError(
                "env backend requires 'field_to_env' in backend_config"
            )
        fte = backend_config["field_to_env"]
        if not isinstance(fte, dict):
            raise InvalidBackendConfigError(
                f"env backend: field_to_env must be a dict; got {type(fte).__name__}"
            )
        for k, v in fte.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise InvalidBackendConfigError(
                    f"env backend: field_to_env entries must be str→str; got {k!r}→{v!r}"
                )


__all__ = ["EnvBackend"]
