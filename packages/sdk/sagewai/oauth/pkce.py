# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PKCE S256 helpers — verifier generation and challenge computation.

Per RFC 7636: ``code_verifier`` is a URL-safe random string of 43-128
characters; ``code_challenge`` (S256 method) is the base64url-encoded
SHA-256 of the verifier, with padding stripped.
"""
from __future__ import annotations

import base64
import hashlib
import secrets


def generate_verifier(length: int = 64) -> str:
    """Return an RFC 7636 ``code_verifier``: URL-safe random of 43-128 chars."""
    if not 43 <= length <= 128:
        raise ValueError("verifier length must be in [43, 128]")
    # secrets.token_urlsafe(n) returns ~1.33n chars; pick n so result is >= length.
    raw = secrets.token_urlsafe(96)[:length]
    return raw


def challenge_for(verifier: str) -> str:
    """Return S256 ``code_challenge`` = base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


__all__ = ["generate_verifier", "challenge_for"]
