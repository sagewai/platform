# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credentials backend error hierarchy.

Each exception carries a stable ``code`` class attribute consumed by
the admin UI to render actionable banners (e.g., "Spotify connection's
env var SAGEWAI_SPOTIFY_CLIENT_SECRET is unset") and by mission logs
for structured error identification. Mirrors the OAuth + Connection
error-hierarchy patterns from PRs #356 and #357.
"""
from __future__ import annotations


class CredentialsError(Exception):
    """Base class for all credentials-backend failures."""

    code = "credentials_error"


class UnknownBackendError(CredentialsError):
    """Backend id is not in the registered set."""

    code = "credentials_unknown_backend"


class MissingEnvVarError(CredentialsError):
    """An env-backend reference points to an unset environment variable."""

    code = "credentials_missing_env_var"


class SopsDecryptError(CredentialsError):
    """SOPS subprocess failed (binary missing, file missing, decrypt failed)."""

    code = "credentials_sops_decrypt_failed"


class BackendUnhealthyError(CredentialsError):
    """Backend.health() returned ok=False."""

    code = "credentials_backend_unhealthy"


class InvalidBackendConfigError(CredentialsError):
    """backend_config dict failed validation."""

    code = "credentials_invalid_config"


# ── Vault backend ────────────────────────────────────────────────────


class VaultError(CredentialsError):
    """Base class for Vault backend failures."""

    code = "credentials_vault_error"


class VaultAuthError(VaultError):
    """Vault token / AppRole authentication failed."""

    code = "credentials_vault_auth_failed"


class VaultReadError(VaultError):
    """Vault KV read failed (missing path, missing key, permission denied)."""

    code = "credentials_vault_read_failed"


class VaultConfigError(InvalidBackendConfigError):
    """VaultBackendConfig validation failed."""

    code = "credentials_vault_invalid_config"


# ── Doppler backend ──────────────────────────────────────────────────


class DopplerError(CredentialsError):
    """Base class for Doppler backend failures."""

    code = "credentials_doppler_error"


class DopplerAuthError(DopplerError):
    """Doppler service-token auth failed (401)."""

    code = "credentials_doppler_auth_failed"


class DopplerApiError(DopplerError):
    """Doppler API call failed (4xx/5xx other than 401, or missing key)."""

    code = "credentials_doppler_api_failed"


class DopplerConfigError(InvalidBackendConfigError):
    """DopplerBackendConfig validation failed."""

    code = "credentials_doppler_invalid_config"


__all__ = [
    "BackendUnhealthyError",
    "CredentialsError",
    "DopplerApiError",
    "DopplerAuthError",
    "DopplerConfigError",
    "DopplerError",
    "InvalidBackendConfigError",
    "MissingEnvVarError",
    "SopsDecryptError",
    "UnknownBackendError",
    "VaultAuthError",
    "VaultConfigError",
    "VaultError",
    "VaultReadError",
]
