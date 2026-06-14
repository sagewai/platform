# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Sagewai LLM request signing.

The headline test, :func:`test_client_signature_matches_server_canonical`, pins
the client signature to the hosted service's exact canonical string. They had
diverged (the client prefixed ``instance_id`` and appended the raw body; the
server uses ``{method}\\n{path}\\n{ts}\\n{sha256(body)}``), so every signed
request was rejected. This test is the contract that keeps them in lockstep.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from sagewai.autopilot.sagewai_llm.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    build_signed_headers,
    sign_request,
    verify_signature,
)

INSTANCE_ID = "aaaabbbbccccddddaaaabbbbccccdddd"
SECRET = "f" * 64  # 32-byte hex key


def _server_sign(*, method: str, path: str, timestamp: str, body: bytes, secret_hex: str) -> str:
    """Re-implementation of sagewai-llm's auth.py canonical + signing.

    Mirrors ``tests/auth/test_strict_mode.py::_sign`` in the sagewai-llm repo
    verbatim — the source of truth the client must agree with.
    """
    secret = bytes.fromhex(secret_hex)
    body_sha = hashlib.sha256(body).hexdigest()
    canonical = f"{method}\n{path}\n{timestamp}\n{body_sha}".encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def test_client_signature_matches_server_canonical():
    """The client signs EXACTLY what the server verifies — the core contract."""
    common = dict(
        method="POST",
        path="/v1/blueprints/generate",
        timestamp="2026-04-16T00:00:00Z",
        body=b'{"goal":"summarise my inbox"}',
    )
    client_sig = sign_request(secret=SECRET, **common)
    server_sig = _server_sign(secret_hex=SECRET, **common)
    assert client_sig == server_sig


def test_signature_excludes_instance_id():
    """instance_id keys the secret server-side; it is NOT in the canonical."""
    base = dict(
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    # Same signature regardless of which instance carries it (only the header
    # differs) — proves instance_id left the canonical string.
    headers_a = build_signed_headers(instance_id="a" * 32, **base)
    headers_b = build_signed_headers(instance_id="b" * 32, **base)
    assert headers_a[SIGNATURE_HEADER] == headers_b[SIGNATURE_HEADER]


def test_sign_is_deterministic_for_same_inputs():
    base = dict(
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    assert sign_request(**base) == sign_request(**base)


@pytest.mark.parametrize(
    "mutation",
    [
        {"timestamp": "2026-04-16T00:00:01Z"},
        {"method": "GET"},
        {"path": "/v1/blueprints/retrieve"},
        {"body": b'{"goal":"y"}'},
    ],
)
def test_sign_differs_when_any_signed_field_mutates(mutation: dict):
    base = dict(
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b'{"goal":"x"}',
    )
    assert sign_request(**base) != sign_request(**{**base, **mutation})


def test_verify_accepts_matching_signature():
    common = dict(
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello",
    )
    sig = sign_request(**common)
    assert verify_signature(expected=sig, **common)


def test_verify_rejects_tampered_body():
    sig = sign_request(
        secret=SECRET,
        timestamp="2026-04-16T00:00:00Z",
        method="POST",
        path="/v1/blueprints/generate",
        body=b"hello",
    )
    assert not verify_signature(
        expected=sig,
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
