# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credentials-backend error hierarchy tests."""
from __future__ import annotations

from sagewai.connections.credentials.errors import (
    BackendUnhealthyError,
    CredentialsError,
    DopplerApiError,
    DopplerAuthError,
    DopplerConfigError,
    DopplerError,
    InvalidBackendConfigError,
    MissingEnvVarError,
    SopsDecryptError,
    UnknownBackendError,
    VaultAuthError,
    VaultConfigError,
    VaultError,
    VaultReadError,
)


def test_all_subclasses_inherit_from_credentials_error():
    for cls in (
        UnknownBackendError,
        MissingEnvVarError,
        SopsDecryptError,
        BackendUnhealthyError,
        InvalidBackendConfigError,
    ):
        assert issubclass(cls, CredentialsError)


def test_stable_error_codes():
    """Stable codes are the contract for admin UI rendering + mission logs."""
    assert CredentialsError.code == "credentials_error"
    assert UnknownBackendError.code == "credentials_unknown_backend"
    assert MissingEnvVarError.code == "credentials_missing_env_var"
    assert SopsDecryptError.code == "credentials_sops_decrypt_failed"
    assert BackendUnhealthyError.code == "credentials_backend_unhealthy"
    assert InvalidBackendConfigError.code == "credentials_invalid_config"


def test_vault_errors_inherit_from_credentials_error():
    for cls in (VaultError, VaultAuthError, VaultReadError):
        assert issubclass(cls, CredentialsError)


def test_vault_config_error_inherits_from_invalid_backend_config():
    assert issubclass(VaultConfigError, InvalidBackendConfigError)


def test_doppler_errors_inherit_from_credentials_error():
    for cls in (DopplerError, DopplerAuthError, DopplerApiError):
        assert issubclass(cls, CredentialsError)


def test_doppler_config_error_inherits_from_invalid_backend_config():
    assert issubclass(DopplerConfigError, InvalidBackendConfigError)


def test_vault_stable_codes():
    assert VaultError.code == "credentials_vault_error"
    assert VaultAuthError.code == "credentials_vault_auth_failed"
    assert VaultReadError.code == "credentials_vault_read_failed"
    assert VaultConfigError.code == "credentials_vault_invalid_config"


def test_doppler_stable_codes():
    assert DopplerError.code == "credentials_doppler_error"
    assert DopplerAuthError.code == "credentials_doppler_auth_failed"
    assert DopplerApiError.code == "credentials_doppler_api_failed"
    assert DopplerConfigError.code == "credentials_doppler_invalid_config"
