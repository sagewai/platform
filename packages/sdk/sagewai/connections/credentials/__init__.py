# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credentials backend abstraction.

PR3 ships the :class:`CredentialsBackend` Protocol + 3 backends
(``local``, ``env``, ``sops``) + :class:`CredentialsBackendRouter`.
PR4 wires the router into ``PluginContext.creds`` at admin-route
construction time and calls ``router.encrypt`` before
``store.create``/``store.update`` + ``router.decrypt`` after
``store.get``/``store.list``.
"""
from sagewai.connections.credentials.base import CredentialsBackend
from sagewai.connections.credentials.doppler import (
    DopplerBackend,
    DopplerBackendConfig,
)
from sagewai.connections.credentials.env import EnvBackend
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
from sagewai.connections.credentials.local import LocalBackend
from sagewai.connections.credentials.router import (
    BACKENDS,
    CredentialsBackendRouter,
    all_backends,
    get_backend,
)
from sagewai.connections.credentials.sops import SopsBackend
from sagewai.connections.credentials.vault import (
    VaultBackend,
    VaultBackendConfig,
)


__all__ = [
    "BACKENDS",
    "BackendUnhealthyError",
    "CredentialsBackend",
    "CredentialsBackendRouter",
    "CredentialsError",
    "DopplerApiError",
    "DopplerAuthError",
    "DopplerBackend",
    "DopplerBackendConfig",
    "DopplerConfigError",
    "DopplerError",
    "EnvBackend",
    "InvalidBackendConfigError",
    "LocalBackend",
    "MissingEnvVarError",
    "SopsBackend",
    "SopsDecryptError",
    "UnknownBackendError",
    "VaultAuthError",
    "VaultBackend",
    "VaultBackendConfig",
    "VaultConfigError",
    "VaultError",
    "VaultReadError",
    "all_backends",
    "get_backend",
]
