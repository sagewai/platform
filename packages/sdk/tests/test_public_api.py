# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests that all public API exports are importable from the top-level package."""

from __future__ import annotations


def test_agent_exports() -> None:
    """All agent classes and helpers are importable."""
    from sagewai import (
        BaseAgent,
        ConditionalAgent,
        GoogleNativeAgent,
        LoopAgent,
        ParallelAgent,
        SequentialAgent,
        UniversalAgent,
        agent_as_tool,
    )

    assert BaseAgent is not None
    assert UniversalAgent is not None
    assert GoogleNativeAgent is not None
    assert SequentialAgent is not None
    assert ParallelAgent is not None
    assert LoopAgent is not None
    assert ConditionalAgent is not None
    assert callable(agent_as_tool)


def test_strategy_exports() -> None:
    """All strategy classes are importable."""
    from sagewai import (
        ExecutionStrategy,
        LATSStrategy,
        PlanningStrategy,
        ReActStrategy,
        RoutingStrategy,
        SelfCorrectionStrategy,
        TreeOfThoughtsStrategy,
    )

    assert ReActStrategy is not None
    assert RoutingStrategy is not None
    assert PlanningStrategy is not None
    assert ExecutionStrategy is not None
    assert LATSStrategy is not None
    assert TreeOfThoughtsStrategy is not None
    assert SelfCorrectionStrategy is not None


def test_safety_exports() -> None:
    """All safety/guardrail classes are importable."""
    from sagewai import (
        ContentFilter,
        Guardrail,
        GuardrailResult,
        GuardrailViolationError,
        HallucinationGuard,
        OutputSchemaGuard,
        PIIGuard,
        TokenBudgetGuard,
    )

    assert Guardrail is not None
    assert GuardrailResult is not None
    assert GuardrailViolationError is not None
    assert ContentFilter is not None
    assert PIIGuard is not None
    assert HallucinationGuard is not None
    assert TokenBudgetGuard is not None
    assert OutputSchemaGuard is not None


def test_workflow_exports() -> None:
    """All workflow classes are importable."""
    from sagewai import (
        ApprovalGate,
        DeadLetterQueue,
        DurableWorkflow,
        WorkflowMonitor,
        WorkflowWorker,
    )

    assert DurableWorkflow is not None
    assert ApprovalGate is not None
    assert WorkflowWorker is not None
    assert WorkflowMonitor is not None
    assert DeadLetterQueue is not None


def test_tool_exports() -> None:
    """Tool decorator, ToolSpec, and McpClient are importable."""
    from sagewai import McpClient, ToolSpec, tool

    assert callable(tool)
    assert ToolSpec is not None
    assert McpClient is not None


def test_model_exports() -> None:
    """Data model classes are importable."""
    from sagewai import AgentConfig, ChatMessage, InferenceParams

    assert ChatMessage is not None
    assert AgentConfig is not None
    assert InferenceParams is not None


def test_connector_exports() -> None:
    """Connector classes are importable."""
    from sagewai import ConnectorRegistry, ConnectorSpec

    assert ConnectorRegistry is not None
    assert ConnectorSpec is not None


def test_error_exports() -> None:
    """Error hierarchy classes are importable."""
    from sagewai import (
        SagewaiConfigError,
        SagewaiError,
        SagewaiLLMError,
        SagewaiRateLimitError,
        SagewaiTimeoutError,
        SagewaiToolError,
        SagewaiWorkflowError,
    )

    assert issubclass(SagewaiLLMError, SagewaiError)
    assert issubclass(SagewaiRateLimitError, SagewaiLLMError)
    assert issubclass(SagewaiTimeoutError, SagewaiError)
    assert issubclass(SagewaiConfigError, SagewaiError)
    assert issubclass(SagewaiWorkflowError, SagewaiError)
    assert issubclass(SagewaiToolError, SagewaiError)


def test_all_list_matches_exports() -> None:
    """__all__ contains every exported name and nothing extra."""
    import sagewai

    all_names = set(sagewai.__all__)
    # Every name in __all__ must be a real attribute
    for name in all_names:
        assert hasattr(sagewai, name), f"{name} in __all__ but not an attribute"
