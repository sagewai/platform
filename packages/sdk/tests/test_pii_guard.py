"""Tests for PII detection and redaction guardrail."""

from __future__ import annotations

import pytest

from sagewai.safety.pii import PIIEntityType, PIIGuard


class TestPIIDetection:
    """Test PII entity detection."""

    @pytest.mark.asyncio
    async def test_detects_email(self):
        guard = PIIGuard(entity_types=[PIIEntityType.EMAIL])
        result = await guard.check_input("Contact me at john@example.com", {})
        assert not result.passed
        assert "EMAIL" in result.violation

    @pytest.mark.asyncio
    async def test_detects_phone(self):
        guard = PIIGuard(entity_types=[PIIEntityType.PHONE])
        result = await guard.check_input("Call me at +1-555-123-4567", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_detects_ssn(self):
        guard = PIIGuard(entity_types=[PIIEntityType.SSN])
        result = await guard.check_input("My SSN is 123-45-6789", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_detects_credit_card(self):
        guard = PIIGuard(entity_types=[PIIEntityType.CREDIT_CARD])
        result = await guard.check_input("Card: 4111-1111-1111-1111", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_detects_iban(self):
        guard = PIIGuard(entity_types=[PIIEntityType.IBAN])
        result = await guard.check_input("IBAN: DE89370400440532013000", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_detects_ip_address(self):
        guard = PIIGuard(entity_types=[PIIEntityType.IP_ADDRESS])
        result = await guard.check_input("Server at 192.168.1.100", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_passes_clean_text(self):
        guard = PIIGuard()  # All entity types
        result = await guard.check_input("The weather is nice today.", {})
        assert result.passed

    @pytest.mark.asyncio
    async def test_detects_multiple_entities(self):
        guard = PIIGuard(entity_types=[PIIEntityType.EMAIL, PIIEntityType.PHONE])
        result = await guard.check_input(
            "Email john@example.com or call +1-555-123-4567", {}
        )
        assert not result.passed


class TestPIIRedaction:
    """Test PII redaction action."""

    @pytest.mark.asyncio
    async def test_redact_email(self):
        guard = PIIGuard(action="redact", entity_types=[PIIEntityType.EMAIL])
        text = "Contact john@example.com for info."
        redacted = guard.redact(text)
        assert "john@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted

    @pytest.mark.asyncio
    async def test_redact_multiple(self):
        guard = PIIGuard(action="redact")
        text = "Email: test@test.com, SSN: 123-45-6789"
        redacted = guard.redact(text)
        assert "test@test.com" not in redacted
        assert "123-45-6789" not in redacted

    @pytest.mark.asyncio
    async def test_redact_preserves_structure(self):
        guard = PIIGuard(action="redact", entity_types=[PIIEntityType.EMAIL])
        text = "Dear user, your email john@example.com has been verified."
        redacted = guard.redact(text)
        assert redacted.startswith("Dear user")
        assert redacted.endswith("has been verified.")


class TestPIIActions:
    """Test different guardrail actions."""

    @pytest.mark.asyncio
    async def test_block_action(self):
        guard = PIIGuard(action="block")
        result = await guard.check_input("Email: test@test.com", {})
        assert not result.passed
        assert result.action == "block"

    @pytest.mark.asyncio
    async def test_warn_action(self):
        guard = PIIGuard(action="warn")
        result = await guard.check_input("Email: test@test.com", {})
        assert not result.passed
        assert result.action == "warn"

    @pytest.mark.asyncio
    async def test_escalate_action(self):
        guard = PIIGuard(action="escalate")
        result = await guard.check_input("Email: test@test.com", {})
        assert not result.passed
        assert result.action == "escalate"

    @pytest.mark.asyncio
    async def test_log_only_action(self):
        guard = PIIGuard(action="log_only")
        result = await guard.check_input("Email: test@test.com", {})
        # log_only still detects but passes (action is "warn" for guardrail compat)
        assert not result.passed
        assert result.action == "warn"


class TestPIIAuditEvents:
    """Test PII audit event emission."""

    @pytest.mark.asyncio
    async def test_audit_event_data(self):
        guard = PIIGuard(entity_types=[PIIEntityType.EMAIL])
        result = await guard.check_input("Email: test@test.com", {})
        # The result should contain entity info for audit
        assert "EMAIL" in result.violation

    @pytest.mark.asyncio
    async def test_output_check(self):
        guard = PIIGuard(entity_types=[PIIEntityType.EMAIL])
        result = await guard.check_output("Your email is user@domain.com", {})
        assert not result.passed
