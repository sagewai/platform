"""Tests for sagewai.safety.permissions — tiered permission policy."""

from __future__ import annotations

import pytest

from sagewai.safety.permissions import (
    CLIPrompter,
    PermissionCheckResult,
    PermissionLevel,
    PermissionPolicy,
    PermissionPrompter,
    ScriptedPrompter,
)


# ---------------------------------------------------------------------------
# Sync check() tests
# ---------------------------------------------------------------------------


class TestPermissionLevel:
    """PermissionLevel ordering and values."""

    def test_ordering(self) -> None:
        assert PermissionLevel.NONE < PermissionLevel.READ
        assert PermissionLevel.READ < PermissionLevel.SUGGEST
        assert PermissionLevel.SUGGEST < PermissionLevel.AUTO_APPROVE
        assert PermissionLevel.AUTO_APPROVE < PermissionLevel.ADMIN

    def test_int_values(self) -> None:
        assert int(PermissionLevel.NONE) == 0
        assert int(PermissionLevel.ADMIN) == 4


class TestPermissionPolicyCheck:
    """Sync permission checks via policy.check()."""

    def test_default_allow(self) -> None:
        """Tools pass when no deny rules are configured."""
        policy = PermissionPolicy()
        result = policy.check("any_tool")
        assert result.allowed is True
        assert result.needs_approval is False
        assert result.reason == ""

    def test_exact_deny(self) -> None:
        """deny_names blocks exact tool name matches."""
        policy = PermissionPolicy(deny_names=["delete_all"])
        result = policy.check("delete_all")
        assert result.allowed is False
        assert "denied by name" in result.reason

    def test_exact_deny_no_partial(self) -> None:
        """deny_names does not block partial matches."""
        policy = PermissionPolicy(deny_names=["delete_all"])
        result = policy.check("delete_all_v2")
        assert result.allowed is True

    def test_prefix_deny(self) -> None:
        """deny_prefixes blocks tools starting with the prefix."""
        policy = PermissionPolicy(deny_prefixes=["bash"])
        result = policy.check("bash_exec")
        assert result.allowed is False
        assert "denied by prefix: bash" in result.reason

    def test_prefix_deny_blocks_exact(self) -> None:
        """deny_prefixes blocks the exact prefix string too."""
        policy = PermissionPolicy(deny_prefixes=["bash"])
        result = policy.check("bash")
        assert result.allowed is False

    def test_prefix_deny_no_infix(self) -> None:
        """deny_prefixes does not block tools containing the prefix mid-name."""
        policy = PermissionPolicy(deny_prefixes=["bash"])
        result = policy.check("rebash")
        assert result.allowed is True

    def test_suggest_name(self) -> None:
        """suggest_names returns needs_approval=True."""
        policy = PermissionPolicy(suggest_names=["file_write"])
        result = policy.check("file_write")
        assert result.allowed is True
        assert result.needs_approval is True
        assert "requires approval" in result.reason

    def test_suggest_prefix(self) -> None:
        """suggest_prefixes returns needs_approval=True."""
        policy = PermissionPolicy(suggest_prefixes=["file_"])
        result = policy.check("file_delete")
        assert result.allowed is True
        assert result.needs_approval is True
        assert "prefix: file_" in result.reason

    def test_suggest_prefix_no_infix(self) -> None:
        """suggest_prefixes does not match infix occurrences."""
        policy = PermissionPolicy(suggest_prefixes=["file_"])
        result = policy.check("profile_update")
        assert result.allowed is True
        assert result.needs_approval is False

    def test_tool_levels_allowed(self) -> None:
        """tool_levels allows when current level meets requirement."""
        policy = PermissionPolicy(
            tool_levels={"admin_op": PermissionLevel.ADMIN},
        )
        result = policy.check("admin_op", PermissionLevel.ADMIN)
        assert result.allowed is True

    def test_tool_levels_denied(self) -> None:
        """tool_levels denies when current level is below requirement."""
        policy = PermissionPolicy(
            tool_levels={"admin_op": PermissionLevel.ADMIN},
        )
        result = policy.check("admin_op", PermissionLevel.READ)
        assert result.allowed is False
        assert "requires ADMIN" in result.reason
        assert "current level is READ" in result.reason

    def test_deny_takes_precedence_over_suggest(self) -> None:
        """deny_names takes priority over suggest_names for the same tool."""
        policy = PermissionPolicy(
            deny_names=["risky_tool"],
            suggest_names=["risky_tool"],
        )
        result = policy.check("risky_tool")
        assert result.allowed is False
        assert "denied by name" in result.reason

    def test_deny_prefix_takes_precedence_over_suggest_prefix(self) -> None:
        """deny_prefixes takes priority over suggest_prefixes."""
        policy = PermissionPolicy(
            deny_prefixes=["shell"],
            suggest_prefixes=["shell"],
        )
        result = policy.check("shell_exec")
        assert result.allowed is False
        assert "denied by prefix" in result.reason

    def test_custom_default_level(self) -> None:
        """Default level is used when current_level is not provided."""
        policy = PermissionPolicy(
            default_level=PermissionLevel.READ,
            tool_levels={"admin_op": PermissionLevel.ADMIN},
        )
        result = policy.check("admin_op")
        assert result.allowed is False
        assert result.level == PermissionLevel.READ


# ---------------------------------------------------------------------------
# Async check_and_approve() tests
# ---------------------------------------------------------------------------


class TestCheckAndApprove:
    """Async approval flow via policy.check_and_approve()."""

    @pytest.mark.asyncio
    async def test_approved_with_prompter(self) -> None:
        """ScriptedPrompter approves the tool."""
        prompter = ScriptedPrompter(
            responses={"file_write": True},
        )
        policy = PermissionPolicy(
            suggest_names=["file_write"],
            prompter=prompter,
        )
        result = await policy.check_and_approve("file_write", {"path": "/tmp"})
        assert result.allowed is True
        assert result.needs_approval is False  # cleared after successful approval

    @pytest.mark.asyncio
    async def test_denied_by_prompter(self) -> None:
        """ScriptedPrompter denies the tool."""
        prompter = ScriptedPrompter(
            responses={"file_write": False},
        )
        policy = PermissionPolicy(
            suggest_names=["file_write"],
            prompter=prompter,
        )
        result = await policy.check_and_approve("file_write", {"path": "/tmp"})
        assert result.allowed is False
        assert "User denied approval" in result.reason

    @pytest.mark.asyncio
    async def test_no_prompter_denies_suggest(self) -> None:
        """SUGGEST tool is denied when no prompter is configured."""
        policy = PermissionPolicy(suggest_names=["file_write"])
        result = await policy.check_and_approve("file_write", {"path": "/tmp"})
        assert result.allowed is False
        assert "No prompter configured" in result.reason

    @pytest.mark.asyncio
    async def test_denied_tool_skips_prompter(self) -> None:
        """Denied tools never reach the prompter."""
        prompter = ScriptedPrompter(default=True)
        policy = PermissionPolicy(
            deny_names=["delete_all"],
            prompter=prompter,
        )
        result = await policy.check_and_approve("delete_all", {})
        assert result.allowed is False
        assert "denied by name" in result.reason

    @pytest.mark.asyncio
    async def test_allowed_tool_skips_prompter(self) -> None:
        """Non-suggest allowed tools skip the prompter entirely."""
        prompter = ScriptedPrompter(default=False)
        policy = PermissionPolicy(prompter=prompter)
        result = await policy.check_and_approve("safe_tool", {})
        assert result.allowed is True
        assert result.needs_approval is False

    @pytest.mark.asyncio
    async def test_scripted_prompter_default(self) -> None:
        """ScriptedPrompter falls back to default for unknown tools."""
        prompter = ScriptedPrompter(default=True)
        policy = PermissionPolicy(
            suggest_names=["unknown_tool"],
            prompter=prompter,
        )
        result = await policy.check_and_approve("unknown_tool", {})
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Protocol & helpers
# ---------------------------------------------------------------------------


class TestPrompterProtocol:
    """PermissionPrompter protocol conformance."""

    def test_scripted_is_prompter(self) -> None:
        assert isinstance(ScriptedPrompter(), PermissionPrompter)

    def test_cli_is_prompter(self) -> None:
        assert isinstance(CLIPrompter(), PermissionPrompter)


class TestPermissionCheckResult:
    """PermissionCheckResult dataclass."""

    def test_defaults(self) -> None:
        result = PermissionCheckResult(
            allowed=True, level=PermissionLevel.READ
        )
        assert result.reason == ""
        assert result.needs_approval is False
