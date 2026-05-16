# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Error hierarchy for the Sagewai LLM HTTP client.

All client-specific exceptions inherit from :class:`ClientError`, which
in turn inherits from :class:`sagewai.autopilot.errors.AutopilotError`.
This lets callers write a single ``except ClientError`` to catch any
client failure while still allowing fine-grained handling.
"""

from __future__ import annotations

from sagewai.autopilot.errors import AutopilotError


class ClientError(AutopilotError):
    """Base class for all Sagewai LLM client errors."""


class ClientUnreachable(ClientError):  # noqa: N818
    """Raised when the hosted service cannot be reached at all.

    This covers DNS failures, connection refused, TLS errors, and
    connection timeouts. The client falls back to the local cache where
    possible and raises this only when no cached value is available.
    """


class QuotaExceeded(ClientError):  # noqa: N818
    """Raised when the service returns 429 and the client cannot satisfy
    the request from the local cache.

    Attributes:
        tier:     Current tier name returned by the server.
        limit:    Hard monthly limit for this endpoint on this tier.
        endpoint: Short endpoint identifier (e.g. ``"generate"``).
    """

    def __init__(self, *, tier: str, limit: int, endpoint: str) -> None:
        self.tier = tier
        self.limit = limit
        self.endpoint = endpoint
        super().__init__(f"quota exceeded: tier={tier!r} limit={limit} endpoint={endpoint!r}")


class ServiceError(ClientError):
    """Raised when the service returns a non-retryable error (4xx other
    than 429, or persistent 5xx after retries).

    Attributes:
        status_code: HTTP status code returned by the server.
        body:        Response body (trimmed).
    """

    def __init__(self, *, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:500] if body else ""
        super().__init__(f"service error {status_code}: {self.body}")


class SignatureError(ClientError):
    """Raised when a request signature check fails.

    This should never happen on a request *produced* by this client — it
    can be raised by the signature verification helper used in tests or
    by the server-side handler.
    """
