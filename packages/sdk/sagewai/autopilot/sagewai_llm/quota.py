# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Quota status + ``X-Sagewai-Quota`` response header parsing.

The server returns an ``X-Sagewai-Quota`` header on every response with
the format::

    tier=<name>;endpoint=<name>;used=<int>;limit=<int>;reset=<iso8601>

Key order is not significant. Unknown keys are ignored. Malformed or
missing headers parse to ``None`` so callers can decide whether to
treat it as a soft failure.
"""

from __future__ import annotations

from dataclasses import dataclass

QUOTA_HEADER = "X-Sagewai-Quota"

_REQUIRED_KEYS = {"tier", "endpoint", "used", "limit", "reset"}


@dataclass(frozen=True)
class QuotaStatus:
    """A snapshot of the server's quota accounting for this request."""

    tier: str
    endpoint: str
    used: int
    limit: int
    reset_at: str

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def is_exhausted(self) -> bool:
        return self.used >= self.limit


def parse_quota_header(value: str | None) -> QuotaStatus | None:
    """Parse an ``X-Sagewai-Quota`` header value.

    Returns ``None`` if the header is missing, malformed, or is missing
    any required key.
    """
    if not value:
        return None
    pairs: dict[str, str] = {}
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        pairs[key.strip()] = val.strip()
    if not _REQUIRED_KEYS.issubset(pairs):
        return None
    try:
        used = int(pairs["used"])
        limit = int(pairs["limit"])
    except ValueError:
        return None
    return QuotaStatus(
        tier=pairs["tier"],
        endpoint=pairs["endpoint"],
        used=used,
        limit=limit,
        reset_at=pairs["reset"],
    )
