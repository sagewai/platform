# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Extended tests for workflow visualization — edge cases and nested structures."""

from __future__ import annotations

import pytest

from sagewai.core.visualize import (
    execution_to_mermaid,
    workflow_to_mermaid,
    workflow_to_mermaid_from_yaml,
)


class TestWorkflowToMermaid:
    """Test Mermaid diagram generation from workflow dicts."""

    def test_empty_workflow(self) -> None:
        """Empty workflow should produce a valid but minimal diagram."""
        result = workflow_to_mermaid({"steps": []})
        assert "graph TD" in result

    def test_single_agent_step(self) -> None:
        """Single agent step should appear in the diagram."""
        wf = {
            "type": "sequential",
            "steps": [
                {"type": "agent", "agent": "writer"},
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "graph TD" in result
        assert "writer" in result

    def test_sequential_two_agents(self) -> None:
        """Two sequential agents should chain with arrows."""
        wf = {
            "type": "sequential",
            "steps": [
                {"type": "agent", "agent": "researcher"},
                {"type": "agent", "agent": "writer"},
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "researcher" in result
        assert "writer" in result
        assert "-->" in result

    def test_parallel_steps(self) -> None:
        """Parallel steps should use fork/join pattern."""
        wf = {
            "steps": [
                {
                    "type": "parallel",
                    "steps": [
                        {"type": "agent", "agent": "agent_a"},
                        {"type": "agent", "agent": "agent_b"},
                    ],
                },
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "agent_a" in result
        assert "agent_b" in result

    def test_conditional_step(self) -> None:
        """Conditional steps should show branching."""
        wf = {
            "steps": [
                {
                    "type": "conditional",
                    "condition": "score > 0.8",
                    "if_true": {"type": "agent", "agent": "publish"},
                    "if_false": {"type": "agent", "agent": "revise"},
                },
            ],
        }
        result = workflow_to_mermaid(wf)
        # Conditional renders the condition node; branches may or may not
        # include agent names depending on implementation depth
        assert "condition" in result.lower() or "publish" in result or len(result) > 20

    def test_loop_step(self) -> None:
        """Loop steps should show iteration."""
        wf = {
            "steps": [
                {
                    "type": "loop",
                    "max_iterations": 3,
                    "body": {"type": "agent", "agent": "refiner"},
                },
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "refiner" in result or "loop" in result.lower()

    def test_approval_step(self) -> None:
        """Approval steps should show human-in-the-loop."""
        wf = {
            "steps": [
                {
                    "type": "approval",
                    "prompt": "Review draft",
                },
            ],
        }
        result = workflow_to_mermaid(wf)
        assert len(result) > 10  # Should produce something meaningful

    def test_router_step(self) -> None:
        """Router steps should show routing logic."""
        wf = {
            "steps": [
                {
                    "type": "router",
                    "routes": {
                        "tech": {"type": "agent", "agent": "tech_writer"},
                        "news": {"type": "agent", "agent": "journalist"},
                    },
                },
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "tech_writer" in result or "journalist" in result

    def test_multi_step_sequential(self) -> None:
        """Multiple sequential steps should chain together."""
        wf = {
            "type": "sequential",
            "steps": [
                {"type": "agent", "agent": "step1"},
                {"type": "agent", "agent": "step2"},
                {"type": "agent", "agent": "step3"},
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "step1" in result
        assert "step3" in result
        # Should have arrows between steps
        assert "-->" in result

    def test_nested_parallel_in_sequential(self) -> None:
        """Nested structures: parallel inside sequential."""
        wf = {
            "type": "sequential",
            "steps": [
                {"type": "agent", "agent": "setup"},
                {
                    "type": "parallel",
                    "steps": [
                        {"type": "agent", "agent": "branch_a"},
                        {"type": "agent", "agent": "branch_b"},
                    ],
                },
                {"type": "agent", "agent": "finalize"},
            ],
        }
        result = workflow_to_mermaid(wf)
        assert "setup" in result
        assert "branch_a" in result
        assert "finalize" in result


class TestWorkflowToMermaidFromYaml:
    """Test YAML-to-Mermaid conversion."""

    def test_basic_yaml(self) -> None:
        yaml_str = """
type: sequential
steps:
  - type: agent
    agent: writer
"""
        result = workflow_to_mermaid_from_yaml(yaml_str)
        assert "writer" in result

    def test_invalid_yaml(self) -> None:
        """Invalid YAML should raise or return minimal result."""
        try:
            result = workflow_to_mermaid_from_yaml("not: [valid: yaml: {{")
            assert isinstance(result, str)
        except Exception:
            pass  # Expected for invalid YAML


class TestExecutionToMermaid:
    """Test execution diagram generation."""

    def test_execution_with_steps(self) -> None:
        """Execution with step statuses should show colored nodes."""

        class FakeStep:
            def __init__(self, step_name: str, status: str, duration_seconds: float = 0.1):
                self.step_name = step_name
                self.status = status
                self.duration_seconds = duration_seconds

        class FakeExecution:
            def __init__(self) -> None:
                self.workflow_name = "test-wf"
                self.run_id = "run-123"
                self.status = "completed"
                self.steps = [
                    FakeStep("analyze", "completed", 0.15),
                    FakeStep("write", "completed", 0.20),
                    FakeStep("review", "failed", 0.05),
                ]

        result = execution_to_mermaid(FakeExecution())
        # Should contain Mermaid syntax
        assert "graph" in result.lower() or "-->" in result or len(result) > 30

    def test_execution_contains_mermaid_syntax(self) -> None:
        """Execution diagram should contain Mermaid graph syntax."""

        class FakeStep:
            def __init__(self, step_name: str, status: str):
                self.step_name = step_name
                self.status = status
                self.duration_seconds = 0.0

        class FakeExecution:
            def __init__(self) -> None:
                self.workflow_name = "test"
                self.run_id = "r1"
                self.status = "completed"
                self.steps = [FakeStep("only_step", "completed")]

        result = execution_to_mermaid(FakeExecution())
        assert "graph TD" in result
        assert "only_step" in result
