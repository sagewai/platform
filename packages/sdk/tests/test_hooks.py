"""Tests for sagewai.core.hooks — HookRunner pre/post tool execution hooks."""

from __future__ import annotations

import pytest

from sagewai.core.hooks import (
    HookAction,
    HookContext,
    HookResult,
    HookRunner,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    tool_name: str = "search",
    arguments: dict | None = None,
    agent_name: str = "test-agent",
) -> HookContext:
    return HookContext(
        tool_name=tool_name,
        arguments=arguments or {"query": "hello"},
        agent_name=agent_name,
    )


# ---------------------------------------------------------------------------
# 1. Pre-hook allow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_hook_allow() -> None:
    runner = HookRunner()

    @runner.pre_tool
    async def allow_all(ctx: HookContext) -> HookResult:
        return HookResult.allow()

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.ALLOW


# ---------------------------------------------------------------------------
# 2. Pre-hook deny
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_hook_deny() -> None:
    runner = HookRunner()

    @runner.pre_tool
    async def deny_all(ctx: HookContext) -> HookResult:
        return HookResult.deny("blocked")

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.DENY
    assert result.message == "blocked"


# ---------------------------------------------------------------------------
# 3. Pre-hook modify args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_hook_modify_args() -> None:
    runner = HookRunner()

    @runner.pre_tool
    async def inject_limit(ctx: HookContext) -> HookResult:
        new_args = {**ctx.arguments, "limit": 10}
        return HookResult.modify_args(new_args)

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.MODIFY
    assert result.modified_arguments == {"query": "hello", "limit": 10}


# ---------------------------------------------------------------------------
# 4. Post-hook modify result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_hook_modify_result() -> None:
    runner = HookRunner()

    @runner.post_tool
    async def redact(ctx: HookContext) -> HookResult:
        return HookResult.modify_result("[REDACTED]")

    result = await runner.run_post_hooks(
        _make_context(), tool_result="secret data"
    )
    assert result.action == HookAction.MODIFY
    assert result.modified_result == "[REDACTED]"


# ---------------------------------------------------------------------------
# 5. Multiple hooks — first deny wins
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_pre_hooks_first_deny_wins() -> None:
    runner = HookRunner()
    call_order: list[str] = []

    @runner.pre_tool
    async def hook_a(ctx: HookContext) -> HookResult:
        call_order.append("a")
        return HookResult.allow()

    @runner.pre_tool
    async def hook_b(ctx: HookContext) -> HookResult:
        call_order.append("b")
        return HookResult.deny("b denied")

    @runner.pre_tool
    async def hook_c(ctx: HookContext) -> HookResult:
        call_order.append("c")
        return HookResult.allow()

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.DENY
    assert result.message == "b denied"
    # Hook c should NOT have been called
    assert call_order == ["a", "b"]


# ---------------------------------------------------------------------------
# 6. Async and sync hooks both work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_hook() -> None:
    runner = HookRunner()

    @runner.pre_tool
    def sync_allow(ctx: HookContext) -> HookResult:
        return HookResult.allow()

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.ALLOW


@pytest.mark.asyncio
async def test_sync_and_async_hooks_together() -> None:
    runner = HookRunner()

    @runner.pre_tool
    def sync_hook(ctx: HookContext) -> HookResult:
        return HookResult.modify_args({**ctx.arguments, "sync": True})

    @runner.pre_tool
    async def async_hook(ctx: HookContext) -> HookResult:
        return HookResult.modify_args({**ctx.arguments, "async": True})

    result = await runner.run_pre_hooks(_make_context())
    assert result.action == HookAction.MODIFY
    # Both modifications should accumulate (last wins per round)
    assert result.modified_arguments is not None
    assert result.modified_arguments.get("async") is True


# ---------------------------------------------------------------------------
# 7. Hook exception is caught and logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_hook_exception_is_caught(caplog: pytest.LogCaptureFixture) -> None:
    runner = HookRunner()

    @runner.pre_tool
    async def bad_hook(ctx: HookContext) -> HookResult:
        raise RuntimeError("boom")

    @runner.pre_tool
    async def good_hook(ctx: HookContext) -> HookResult:
        return HookResult.allow()

    result = await runner.run_pre_hooks(_make_context())
    # Should still allow because the good hook passes
    assert result.action == HookAction.ALLOW
    assert "Pre-tool hook failed" in caplog.text


@pytest.mark.asyncio
async def test_post_hook_exception_is_caught(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = HookRunner()

    @runner.post_tool
    async def bad_post(ctx: HookContext) -> HookResult:
        raise ValueError("bad value")

    result = await runner.run_post_hooks(_make_context(), tool_result="ok")
    assert result.action == HookAction.ALLOW
    assert "Post-tool hook failed" in caplog.text


# ---------------------------------------------------------------------------
# 8. Decorator registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decorator_registration() -> None:
    runner = HookRunner()

    @runner.pre_tool
    async def my_pre(ctx: HookContext) -> HookResult:
        return HookResult.allow()

    @runner.post_tool
    async def my_post(ctx: HookContext) -> HookResult:
        return HookResult.allow()

    assert len(runner._pre_hooks) == 1
    assert len(runner._post_hooks) == 1
    # Decorator returns the original function
    assert my_pre is runner._pre_hooks[0]
    assert my_post is runner._post_hooks[0]


# ---------------------------------------------------------------------------
# 9. Programmatic registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_programmatic_registration() -> None:
    runner = HookRunner()

    async def pre_cb(ctx: HookContext) -> HookResult:
        return HookResult.deny("nope")

    async def post_cb(ctx: HookContext) -> HookResult:
        return HookResult.modify_result("changed")

    runner.add_pre_hook(pre_cb)
    runner.add_post_hook(post_cb)

    pre_result = await runner.run_pre_hooks(_make_context())
    assert pre_result.action == HookAction.DENY

    post_result = await runner.run_post_hooks(
        _make_context(), tool_result="original"
    )
    assert post_result.action == HookAction.MODIFY
    assert post_result.modified_result == "changed"


# ---------------------------------------------------------------------------
# 10. has_hooks property
# ---------------------------------------------------------------------------

def test_has_hooks_empty() -> None:
    runner = HookRunner()
    assert runner.has_hooks is False


def test_has_hooks_with_pre() -> None:
    runner = HookRunner()
    runner.add_pre_hook(lambda ctx: HookResult.allow())
    assert runner.has_hooks is True


def test_has_hooks_with_post() -> None:
    runner = HookRunner()
    runner.add_post_hook(lambda ctx: HookResult.allow())
    assert runner.has_hooks is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_hooks_returns_allow() -> None:
    runner = HookRunner()
    pre = await runner.run_pre_hooks(_make_context())
    assert pre.action == HookAction.ALLOW

    post = await runner.run_post_hooks(_make_context(), tool_result="ok")
    assert post.action == HookAction.ALLOW


@pytest.mark.asyncio
async def test_post_hook_receives_result_in_metadata() -> None:
    runner = HookRunner()
    captured_meta: dict = {}

    @runner.post_tool
    async def capture(ctx: HookContext) -> HookResult:
        captured_meta.update(ctx.metadata)
        return HookResult.allow()

    await runner.run_post_hooks(_make_context(), tool_result="the-result")
    assert captured_meta["result"] == "the-result"


@pytest.mark.asyncio
async def test_multiple_post_hooks_chain_modifications() -> None:
    runner = HookRunner()

    @runner.post_tool
    async def append_a(ctx: HookContext) -> HookResult:
        return HookResult.modify_result(ctx.metadata["result"] + "+A")

    @runner.post_tool
    async def append_b(ctx: HookContext) -> HookResult:
        return HookResult.modify_result(ctx.metadata["result"] + "+B")

    result = await runner.run_post_hooks(_make_context(), tool_result="base")
    assert result.action == HookAction.MODIFY
    assert result.modified_result == "base+A+B"


@pytest.mark.asyncio
async def test_deny_default_message() -> None:
    result = HookResult.deny()
    assert result.message == "Tool execution denied by hook"
