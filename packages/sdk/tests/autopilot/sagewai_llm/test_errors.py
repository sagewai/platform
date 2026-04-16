"""Tests for the Sagewai LLM client error hierarchy."""

from __future__ import annotations

import pytest

from sagewai.autopilot.errors import AutopilotError
from sagewai.autopilot.sagewai_llm.errors import (
    ClientError,
    ClientUnreachable,
    QuotaExceeded,
    ServiceError,
    SignatureError,
)


def test_client_error_inherits_autopilot_error():
    assert issubclass(ClientError, AutopilotError)


@pytest.mark.parametrize(
    "cls",
    [ClientUnreachable, QuotaExceeded, ServiceError, SignatureError],
)
def test_all_subclasses_inherit_client_error(cls: type[Exception]) -> None:
    assert issubclass(cls, ClientError)


def test_client_unreachable_carries_reason():
    err = ClientUnreachable("DNS failure")
    assert "DNS failure" in str(err)


def test_quota_exceeded_carries_tier_and_limit():
    err = QuotaExceeded(tier="anonymous", limit=50, endpoint="generate")
    assert err.tier == "anonymous"
    assert err.limit == 50
    assert err.endpoint == "generate"
    assert "anonymous" in str(err)
    assert "50" in str(err)


def test_service_error_carries_status_code_and_body():
    err = ServiceError(status_code=502, body="upstream failure")
    assert err.status_code == 502
    assert "502" in str(err)


def test_signature_error_is_raised_on_tampering():
    err = SignatureError("HMAC mismatch")
    assert "HMAC" in str(err)
