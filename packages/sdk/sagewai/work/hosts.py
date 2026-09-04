# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Host normalization and matching for scoped browser grants."""

from __future__ import annotations

import ipaddress
import re

HTTP_ALLOWED_PORTS = frozenset({80, 443})

_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def idna_allowed_host(host: str) -> str:
    host = host.lower()
    if host.startswith("."):
        return f".{idna_host(host[1:])}"
    return idna_host(host)


def idna_host(host: str) -> str:
    host = host.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna").decode("ascii").lower()
        labels = encoded.split(".")
        if (
            not encoded
            or len(encoded) > 253
            or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
        ):
            raise ValueError("invalid hostname")
        return encoded
    return host


def host_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Match a host against entries normalized with idna_allowed_host."""
    host = idna_host(hostname)
    for allowed in allowed_hosts:
        if allowed.startswith(".") and host.endswith(allowed):
            return True
        if host == allowed:
            return True
    return False
