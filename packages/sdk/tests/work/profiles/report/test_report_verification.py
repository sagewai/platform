# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Every deterministic, containerless report check (spec section 12 step 2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.work.profiles.report.models import ReportArchive, ReportClaim, SourceSnapshot
from sagewai.work.profiles.report.verification import redact_text, verify_report

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
BODY = "# Summary\n\nVendor A shipped a queue.\n\n# Sources\n\n- https://a.example/blog\n"


def _archive(**overrides) -> ReportArchive:
    values = {
        "report_ref": "artifact://sha256:" + "a" * 64,
        "report_bytes": len(BODY.encode("utf-8")),
        "report_sha256": "a" * 64,
        "snapshots": (
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://a.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
        "claims": (
            ReportClaim(
                statement="Vendor A shipped a queue.",
                snapshot_refs=("artifact://sha256:" + "b" * 64,),
            ),
        ),
    }
    values.update(overrides)
    return ReportArchive(**values)


def _verify(body: str = BODY, archive: ReportArchive | None = None, **overrides):
    kwargs = {
        "required_sections": ("Summary",),
        "max_bytes": 200_000,
        "allowed_hosts": ("a.example",),
    }
    kwargs.update(overrides)
    return verify_report(body, archive or _archive(), **kwargs)


def test_a_grounded_report_passes_every_check() -> None:
    assert _verify() == ()


def test_a_report_over_max_bytes_fails() -> None:
    failures = _verify(max_bytes=10)
    assert failures and "over the 10 limit" in failures[0]


def test_a_missing_required_section_fails() -> None:
    assert _verify(required_sections=("Summary", "Risks")) == (
        "required section 'Risks' is missing",
    )


def test_a_claim_with_no_snapshot_fails() -> None:
    archive = _archive(
        claims=(ReportClaim(statement="Vendor B shipped nothing.", snapshot_refs=()),)
    )
    failures = _verify(archive=archive)
    assert failures == ("claim 'Vendor B shipped nothing.' cites no source snapshot",)


def test_a_cited_url_outside_the_allowed_hosts_fails() -> None:
    failures = _verify(allowed_hosts=("b.example",))
    assert failures == ("source https://a.example/blog is outside the grant's allowed hosts",)


def test_a_bare_allowed_host_does_not_allow_a_subdomain() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://news.a.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive) == (
        "source https://news.a.example/blog is outside the grant's allowed hosts",
    )


def test_a_leading_dot_allowed_host_allows_a_subdomain() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://news.a.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive, allowed_hosts=(".a.example",)) == ()


def test_a_source_on_a_disallowed_port_fails() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://a.example:8080/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive) == (
        "source https://a.example:8080/blog uses disallowed port 8080",
    )


def test_a_source_with_a_wrong_scheme_fails_with_the_reason() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="ftp://a.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive) == (
        "source ftp://a.example/blog uses unsupported scheme 'ftp'",
    )


def test_a_source_with_a_malformed_host_fails_with_the_reason() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://bad_host.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive) == (
        "source https://bad_host.example/blog has invalid host: invalid hostname",
    )


def test_a_unicode_allowed_host_matches_a_punycode_source_host() -> None:
    archive = _archive(
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://xn--exmple-cua.com/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
    )
    assert _verify(archive=archive, allowed_hosts=("exämple.com",)) == ()


def test_an_indented_required_heading_counts() -> None:
    assert _verify(body="  # Summary\n\nVendor A shipped a queue.\n") == ()


def test_a_heading_inside_a_fenced_code_block_does_not_count() -> None:
    body = "```python\n# Summary\n```\n"
    assert _verify(body=body) == ("required section 'Summary' is missing",)


def test_a_bearer_token_or_private_key_block_fails() -> None:
    leaked = BODY + "\nAuthorization: Bearer sk-live-1234567890\n"
    assert _verify(body=leaked) == ("report contains a bearer token",)
    key = BODY + "\n-----BEGIN RSA PRIVATE KEY-----\n"
    assert _verify(body=key) == ("report contains a private key block",)


@pytest.mark.parametrize(
    ("label", "positive", "near_miss"),
    [
        ("github token", "ghp_" + "a" * 16, "ghp_" + "a" * 15),
        ("aws access key", "AKIA" + "A" * 16, "AKIA" + "A" * 15),
        ("slack token", "xoxb-" + "a" * 10, "xoxb-" + "a" * 9),
    ],
)
def test_secret_patterns_have_positive_and_near_miss_cases(
    label: str, positive: str, near_miss: str
) -> None:
    assert _verify(body=BODY + positive) == (f"report contains a {label}",)
    assert _verify(body=BODY + near_miss) == ()


def test_credential_values_are_redacted_before_the_artifact_is_stored() -> None:
    token = "ghp_" + "a" * 16
    body = f"The token is {token} and the key is s3cr3t."
    assert redact_text(body, {"GITHUB_TOKEN": token, "API_KEY": "s3cr3t"}) == (
        "The token is [REDACTED:GITHUB_TOKEN] and the key is [REDACTED:API_KEY]."
    )


def test_redaction_ignores_empty_values() -> None:
    assert redact_text("abc", {"EMPTY": ""}) == "abc"


def test_redaction_replaces_the_longest_value_first() -> None:
    assert redact_text("abcd", {"SHORT": "ab", "LONG": "abcd"}) == "[REDACTED:LONG]"
