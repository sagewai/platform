# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PKCE verifier/challenge tests."""
from __future__ import annotations

import base64
import hashlib
import re

import pytest

from sagewai.oauth.pkce import challenge_for, generate_verifier


def test_verifier_format():
    v = generate_verifier()
    # RFC 7636: 43-128 chars, URL-safe alphabet
    assert 43 <= len(v) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", v)


def test_verifier_is_random():
    a = generate_verifier()
    b = generate_verifier()
    assert a != b


def test_challenge_is_s256_of_verifier():
    v = generate_verifier()
    c = challenge_for(v)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert c == expected


def test_verifier_length_bounds():
    with pytest.raises(ValueError):
        generate_verifier(length=42)
    with pytest.raises(ValueError):
        generate_verifier(length=129)
    # 43 and 128 are allowed
    assert len(generate_verifier(length=43)) == 43
    assert len(generate_verifier(length=128)) == 128
