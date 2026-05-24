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


__all__ = [
    "BackendUnhealthyError",
    "CredentialsError",
    "InvalidBackendConfigError",
    "MissingEnvVarError",
    "SopsDecryptError",
    "UnknownBackendError",
]
