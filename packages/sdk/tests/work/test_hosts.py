# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Host normalization and matching rules shared by browser-scoped work tools."""

from __future__ import annotations

import pytest

from sagewai.work.hosts import (
    HTTP_ALLOWED_PORTS,
    host_allowed,
    idna_allowed_host,
    idna_host,
)


def test_exact_allowed_host_matches_only_the_exact_host() -> None:
    assert host_allowed("a.example", ("a.example",))
    assert not host_allowed("news.a.example", ("a.example",))


def test_leading_dot_allowed_host_matches_only_suffix_hosts() -> None:
    assert host_allowed("news.a.example", (".a.example",))
    assert not host_allowed("a.example", (".a.example",))


def test_unicode_hosts_are_normalized_to_punycode() -> None:
    assert idna_host("exämple.com") == "xn--exmple-cua.com"
    assert idna_allowed_host(".exämple.com") == ".xn--exmple-cua.com"
    assert host_allowed("news.xn--exmple-cua.com", (".xn--exmple-cua.com",))


def test_http_allowed_ports_are_80_and_443() -> None:
    assert HTTP_ALLOWED_PORTS == frozenset({80, 443})


def test_invalid_hostname_labels_are_refused() -> None:
    with pytest.raises(ValueError, match="invalid hostname"):
        idna_host("bad_host.example")


def test_hostnames_are_limited_to_253_bytes() -> None:
    valid = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61))
    invalid = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 62))
    assert len(valid.encode("ascii")) == 253
    assert idna_host(valid) == valid
    with pytest.raises(ValueError, match="invalid hostname"):
        idna_host(invalid)
