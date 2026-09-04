# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic, containerless report verification (spec section 12 step 2)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from sagewai.work.hosts import (
    HTTP_ALLOWED_PORTS,
    host_allowed,
    idna_allowed_host,
    idna_host,
)
from sagewai.work.profiles.report.models import ReportArchive

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"(?i)\bauthorization:\s*bearer\s+\S+")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
)


def redact_text(text: str, values: Mapping[str, str]) -> str:
    """Replace every non-empty scoped credential value with its marker, longest first."""
    for name, value in sorted(
        ((name, value) for name, value in values.items() if value),
        key=lambda item: len(item[1]),
        reverse=True,
    ):
        text = text.replace(value, f"[REDACTED:{name}]")
    return text


def _headings(body: str) -> set[str]:
    headings: set[str] = set()
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            headings.add(stripped.lstrip("#").strip().casefold())
    return headings


def _source_failure(url: str, allowed_hosts: tuple[str, ...]) -> str | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError as exc:
        return f"has invalid host: {exc}"
    if parsed.scheme not in {"http", "https"}:
        return f"uses unsupported scheme {parsed.scheme!r}"
    if not host:
        return "has invalid host: host is required"
    try:
        host = idna_host(host)
    except (UnicodeError, ValueError) as exc:
        return f"has invalid host: {exc}"
    try:
        port = parsed.port
    except ValueError as exc:
        return f"uses invalid port: {exc}"
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in HTTP_ALLOWED_PORTS:
        return f"uses disallowed port {port}"
    if not host_allowed(host, allowed_hosts):
        return "is outside the grant's allowed hosts"
    return None


def verify_report(
    body: str,
    archive: ReportArchive,
    *,
    required_sections: Sequence[str],
    max_bytes: int,
    allowed_hosts: Sequence[str],
) -> tuple[str, ...]:
    """Every section-12 check; returns the failures, empty when the report passes."""
    failures: list[str] = []
    if archive.report_bytes > max_bytes:
        failures.append(f"report is {archive.report_bytes} bytes, over the {max_bytes} limit")
    headings = _headings(body)
    failures.extend(
        f"required section {section!r} is missing"
        for section in required_sections
        if section.casefold() not in headings
    )
    known = {snapshot.snapshot_ref for snapshot in archive.snapshots}
    for claim in archive.claims:
        if not any(ref in known for ref in claim.snapshot_refs):
            failures.append(f"claim {claim.statement[:80]!r} cites no source snapshot")
    scoped_hosts = tuple(idna_allowed_host(str(host)) for host in allowed_hosts)
    for snapshot in archive.snapshots:
        failure = _source_failure(snapshot.url, scoped_hosts)
        if failure is not None:
            failures.append(f"source {snapshot.url} {failure}")
    failures.extend(
        f"report contains a {label}"
        for label, pattern in FORBIDDEN_PATTERNS
        if pattern.search(body)
    )
    return tuple(failures)


__all__ = [
    "FORBIDDEN_PATTERNS",
    "redact_text",
    "verify_report",
]
