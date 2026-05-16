# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Sagewai LLM request signing."""

from __future__ import annotations

import pytest

from sagewai.autopilot.sagewai_llm.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    build_signed_headers,
    sign_request,
    verify_signature,
)

INSTANCE_ID = "aaaabbbbccccddddaaaabbbbccccdddd"
SECRET = "f" * 64


def test_sign_is_deterministic_for_same_inputs():
    a = sign_request(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    b = sign_request(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    assert a == b


@pytest.mark.parametrize(
    "mutation",
    [
        {"instance_id": "x" * 32},
        {"timestamp": "2026-04-16T00:00:01Z"},
        {"method": "GET"},
        {"path": "/v1/blueprints/retrieve"},
        {"body": b'{"goal":"y"}'},
    ],
)
def test_sign_differs_when_any_field_mutates(mutation: dict):
    base = dict(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    sig_a = sign_request(**base)
    sig_b = sign_request(**{**base, **mutation})
    assert sig_a != sig_b


def test_verify_accepts_matching_signature():
    sig = sign_request(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello",
    )
    assert verify_signature(
        expected=sig,
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello",
    )


def test_verify_rejects_tampered_body():
    sig = sign_request(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello",
    )
    assert not verify_signature(
        expected=sig,
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello-tampered",
    )


def test_build_signed_headers_adds_all_three_headers():
    headers = build_signed_headers(
        instance_id=INSTANCE_ID,
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"{}",
    )
    assert headers["X-Sagewai-Instance"] == INSTANCE_ID
    assert headers[TIMESTAMP_HEADER] == "2026-04-16T00:00:00Z"
    assert SIGNATURE_HEADER in headers
    assert len(headers[SIGNATURE_HEADER]) == 64  # hex sha256
