# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""HMAC-SHA256 request signing for the Sagewai LLM client.

The signing scheme is::

    canonical = f"{instance_id}\\n{timestamp}\\n{method}\\n{path}\\n".encode()
                + body
    signature = hmac.new(secret_bytes, canonical, sha256).hexdigest()

Signature is sent as the ``X-Sagewai-Signature`` header. The server
reconstructs the canonical string from the request and verifies with
the same secret held server-side.

This module is pure functions — no state, no I/O, no randomness.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Sagewai-Signature"
TIMESTAMP_HEADER = "X-Sagewai-Timestamp"
INSTANCE_HEADER = "X-Sagewai-Instance"


def _canonical(
    *,
    instance_id: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> bytes:
    prefix = f"{instance_id}\n{timestamp}\n{method.upper()}\n{path}\n".encode()
    return prefix + body


def sign_request(
    *,
    instance_id: str,
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    """Return the hex SHA-256 HMAC of the canonical request string."""
    canonical = _canonical(
        instance_id=instance_id,
        timestamp=timestamp,
        method=method,
        path=path,
        body=body,
    )
    return hmac.new(bytes.fromhex(secret), canonical, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    expected: str,
    instance_id: str,
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
) -> bool:
    """Constant-time verify ``expected`` against a freshly computed signature."""
    actual = sign_request(
        instance_id=instance_id,
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
        instance_id=instance_id,
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
