# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Mutual TLS stubs for fleet worker authentication (enterprise tier).

Provides the interface for mTLS-based worker identity verification.
The actual certificate validation logic is deferred to a future enterprise
release; this module defines the configuration model and stub verifier
so that other fleet components can be wired up in advance.

Usage::

    from sagewai.fleet.mtls import MTLSConfig, MTLSVerifier

    config = MTLSConfig(
        ca_cert_path="/etc/fleet/ca.pem",
        server_cert_path="/etc/fleet/server.pem",
        server_key_path="/etc/fleet/server-key.pem",
        require_client_cert=True,
        allowed_cn_patterns=["*.workers.acme.com"],
    )
    verifier = MTLSVerifier(config)
    assert verifier.is_enabled()
    # Stub: always returns None until enterprise implementation
    assert verifier.verify_client_cert("-----BEGIN CERTIFICATE-----...") is None
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MTLSConfig:
    """Configuration for mutual TLS between fleet workers and gateway.

    All paths are optional; ``is_configured()`` returns ``True`` only when
    the CA certificate, server certificate, and server key are all provided.

    Attributes:
        ca_cert_path: Path to the CA certificate for verifying worker certs.
        server_cert_path: Path to the gateway's server certificate.
        server_key_path: Path to the gateway's private key.
        require_client_cert: Whether to require workers to present a client cert.
        allowed_cn_patterns: Glob patterns for allowed Common Name values
            (e.g. ``["*.workers.acme.com", "fleet-worker-*"]``).
    """

    ca_cert_path: str | None = None
    server_cert_path: str | None = None
    server_key_path: str | None = None
    require_client_cert: bool = False
    allowed_cn_patterns: list[str] = field(default_factory=list)

    def is_configured(self) -> bool:
        """Check if all required mTLS paths are set."""
        return bool(
            self.ca_cert_path
            and self.server_cert_path
            and self.server_key_path
        )


class MTLSVerifier:
    """Verifies worker client certificates (stub for enterprise tier).

    Full implementation requires:

    - Certificate pinning against enrolled worker certs
    - Common Name pattern matching for org/pool validation
    - CRL/OCSP checking for certificate revocation
    - Certificate rotation support with graceful rollover

    Currently, all verification methods return stub values. The
    :meth:`is_enabled` check allows callers to skip mTLS logic
    when it is not configured.

    Args:
        config: The mTLS configuration to use.
    """

    def __init__(self, config: MTLSConfig) -> None:
        self._config = config

    @property
    def config(self) -> MTLSConfig:
        """Return the current mTLS configuration."""
        return self._config

    def is_enabled(self) -> bool:
        """Check if mTLS verification is enabled.

        Returns ``True`` only when all certificate paths are configured
        **and** ``require_client_cert`` is set.
        """
        return (
            self._config.is_configured()
            and self._config.require_client_cert
        )

    def verify_client_cert(self, cert_pem: str) -> dict | None:
        """Verify a worker's client certificate.

        Args:
            cert_pem: PEM-encoded client certificate string.

        Returns:
            A claims dict (containing at minimum ``cn`` and ``org``)
            if verification succeeds, or ``None`` if verification fails
            or is not implemented.

        Stub: always returns ``None``. Enterprise implementation would:

        1. Parse the PEM certificate.
        2. Verify the chain against the configured CA.
        3. Extract the Common Name and check against ``allowed_cn_patterns``.
        4. Check CRL/OCSP for revocation status.
        5. Return ``{"cn": "...", "org": "...", "pool": "..."}`` on success.
        """
        if not self.is_enabled():
            logger.debug("mTLS not enabled; skipping certificate verification")
            return None
        # Stub — enterprise implementation goes here
        logger.info("mTLS verify_client_cert called (stub, returning None)")
        return None

    def extract_cn(self, cert_pem: str) -> str | None:
        """Extract the Common Name from a PEM certificate.

        Stub: always returns ``None``.
        """
        return None
