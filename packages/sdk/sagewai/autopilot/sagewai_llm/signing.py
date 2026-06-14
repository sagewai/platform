# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HMAC-SHA256 request signing for the Sagewai LLM client.

The canonical string MUST match the hosted service byte-for-byte
(``sagewai-llm`` ``auth.py:_canonical_string``)::

    canonical = f"{method}\\n{path}\\n{timestamp}\\n{sha256(body).hexdigest()}"
    signature = hmac.new(secret_bytes, canonical, sha256).hexdigest()

The server derives the per-instance secret as ``HKDF(master, instance_id)`` and
re-derives it from the ``X-Sagewai-Instance`` header to verify. The client holds
the same secret (obtained at enrollment — see :mod:`.client`). The instance id
therefore travels in a header and *keys* the secret, but is **not** part of the
signed canonical string — earlier versions wrongly prefixed it, so every
signature was rejected.

``path`` must be the URL path only (no query string), matching the server's
``request.url.path``. The blueprint endpoints this client signs (retrieve,
generate) carry no query, so callers pass the bare path.

This module is pure functions — no state, no I/O, no randomness.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Sagewai-Signature"
TIMESTAMP_HEADER = "X-Sagewai-Timestamp"
INSTANCE_HEADER = "X-Sagewai-Instance"


def _canonical(*, method: str, path: str, timestamp: str, body: bytes) -> bytes:
    body_sha = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_sha}".encode()


def sign_request(
    *,
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    """Return the hex SHA-256 HMAC of the canonical request string."""
    canonical = _canonical(method=method, path=path, timestamp=timestamp, body=body)
    return hmac.new(bytes.fromhex(secret), canonical, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    expected: str,
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> bool:
    """Constant-time verify ``expected`` against a freshly computed signature."""
    actual = sign_request(
        secret=secret,
        timestamp=timestamp,
        method=method,
        path=path,
        body=body,
    )
    return hmac.compare_digest(actual, expected)


def build_signed_headers(
    *,
    instance_id: str,
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> dict[str, str]:
    """Return the three auth headers the hosted service expects."""
    sig = sign_request(
        secret=secret,
        timestamp=timestamp,
        method=method,
        path=path,
        body=body,
    )
    return {
        INSTANCE_HEADER: instance_id,
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sig,
    }
