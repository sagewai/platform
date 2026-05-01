# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for YAML workflow specification parser."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sagewai.core.workflows import LoopAgent, ParallelAgent, SequentialAgent
from sagewai.core.yaml_workflow import (
    WorkflowParseError,
    load_workflow,
    load_workflow_string,
    parse_workflow,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

SIMPLE_SEQUENTIAL = """
name: test-pipeline
description: A test pipeline

agents:
  researcher:
    model: gpt-4o
    system_prompt: You research topics.
  writer:
    model: gpt-4o
    system_prompt: You write content.

workflow:
  type: sequential
  steps:
    - agent: researcher
    - agent: writer
"""

PARALLEL_WORKFLOW = """
name: parallel-test
description: Parallel agents

agents:
  analyst:
    model: gpt-4o
    system_prompt: You analyze data.
  critic:
    model: gpt-4o
    system_prompt: You critique analysis.

workflow:
  type: parallel
  agents: [analyst, critic]
"""

LOOP_WORKFLOW = """
name: loop-test
description: Loop agent

agents:
  refiner:
    model: gpt-4o
    system_prompt: You refine text.

workflow:
  type: loop
  agent: refiner
  max_iterations: 3
  stop_condition: DONE
"""

NESTED_WORKFLOW = """
name: nested-test
description: Nested workflow

agents:
  researcher:
    model: gpt-4o
    system_prompt: You research.
  writer:
    model: gpt-4o
    system_prompt: You write.
  reviewer:
    model: gpt-4o
    system_prompt: You review.

workflow:
  type: sequential
  steps:
    - agent: researcher
    - type: parallel
      agents: [writer, reviewer]
"""

MINIMAL_WORKFLOW = """
name: minimal
agents:
  bot:
    model: gpt-4o
workflow:
  agent: bot
"""


# ---------------------------------------------------------------------------
# parse_workflow tests
# ---------------------------------------------------------------------------


class TestParseWorkflow:
    def test_simple_sequential(self):
        spec = load_workflow_string(SIMPLE_SEQUENTIAL)
        assert spec.name == "test-pipeline"
        assert spec.description == "A test pipeline"
        assert "researcher" in spec.agents
        assert "writer" in spec.agents
        assert isinstance(spec.workflow, SequentialAgent)

    def test_parallel(self):
        spec = load_workflow_string(PARALLEL_WORKFLOW)
        assert spec.name == "parallel-test"
        assert isinstance(spec.workflow, ParallelAgent)
        assert len(spec.workflow.agents) == 2

    def test_loop(self):
        spec = load_workflow_string(LOOP_WORKFLOW)
        assert spec.name == "loop-test"
        assert isinstance(spec.workflow, LoopAgent)
        assert spec.workflow.config.max_iterations == 3

    def test_nested(self):
        spec = load_workflow_string(NESTED_WORKFLOW)
        assert spec.name == "nested-test"
        assert isinstance(spec.workflow, SequentialAgent)
        assert len(spec.workflow.agents) == 2
        # Second step should be a ParallelAgent
        assert isinstance(spec.workflow.agents[1], ParallelAgent)

    def test_minimal_agent_ref(self):
        spec = load_workflow_string(MINIMAL_WORKFLOW)
        assert spec.name == "minimal"
        # Workflow is just a reference to a single agent
        assert spec.workflow.config.name == "bot"

    def test_agent_config_applied(self):
        spec = load_workflow_string(SIMPLE_SEQUENTIAL)
        researcher = spec.agents["researcher"]
        assert researcher.config.model == "gpt-4o"
        assert "research" in researcher.config.system_prompt.lower()

    def test_agent_defaults(self):
        yaml_str = """
name: defaults-test
agents:
  bot: {}
workflow:
  agent: bot
"""
        spec = load_workflow_string(yaml_str)
        bot = spec.agents["bot"]
        assert bot.config.model == "gpt-4o"
        assert bot.config.inference.temperature == 0.7

    def test_agent_null_config(self):
        yaml_str = """
name: null-test
agents:
  bot:
workflow:
  agent: bot
"""
        spec = load_workflow_string(yaml_str)
        assert "bot" in spec.agents


# ---------------------------------------------------------------------------
# Validation error tests
# ---------------------------------------------------------------------------


class TestWorkflowValidation:
    def test_missing_name(self):
        with pytest.raises(WorkflowParseError, match="name"):
            parse_workflow({"agents": {}, "workflow": {"agent": "x"}})

    def test_missing_workflow(self):
        with pytest.raises(WorkflowParseError, match="workflow"):
            parse_workflow({"name": "test", "agents": {}})

    def test_invalid_root(self):
        with pytest.raises(WorkflowParseError, match="mapping"):
            parse_workflow("not a dict")

    def test_unknown_agent_ref(self):
        with pytest.raises(WorkflowParseError, match="Unknown agent"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"agent": "nonexistent"},
                }
            )

    def test_unknown_workflow_type(self):
        with pytest.raises(WorkflowParseError, match="Unknown workflow type"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"type": "invalid"},
                }
            )

    def test_sequential_empty_steps(self):
        with pytest.raises(WorkflowParseError, match="at least one step"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"type": "sequential", "steps": []},
                }
            )

    def test_parallel_empty_agents(self):
        with pytest.raises(WorkflowParseError, match="at least one agent"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"type": "parallel", "agents": []},
                }
            )

    def test_loop_missing_agent(self):
        with pytest.raises(WorkflowParseError, match="must specify an 'agent'"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"type": "loop"},
                }
            )

    def test_node_missing_type_and_agent(self):
        with pytest.raises(WorkflowParseError, match="must have 'type'"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": {"bot": {}},
                    "workflow": {"something": "else"},
                }
            )

    def test_invalid_agents_field(self):
        with pytest.raises(WorkflowParseError, match="'agents' must be a mapping"):
            parse_workflow(
                {
                    "name": "test",
                    "agents": "not a dict",
                    "workflow": {"agent": "x"},
                }
            )


# ---------------------------------------------------------------------------
# File loading tests
# ---------------------------------------------------------------------------


class TestLoadWorkflow:
    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(SIMPLE_SEQUENTIAL)
            f.flush()
            spec = load_workflow(f.name)
        assert spec.name == "test-pipeline"
        assert isinstance(spec.workflow, SequentialAgent)

    def test_load_from_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(PARALLEL_WORKFLOW)
            f.flush()
            spec = load_workflow(Path(f.name))
        assert spec.name == "parallel-test"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_workflow("/nonexistent/path.yaml")


# ---------------------------------------------------------------------------
# WorkflowSpec.run tests
# ---------------------------------------------------------------------------


class TestWorkflowSpecRun:
    @patch("sagewai.core.yaml_workflow.UniversalAgent")
    async def test_run(self, mock_cls):
        mock_agent = AsyncMock()
        mock_agent.chat.return_value = "result"
        mock_agent.config.name = "bot"
        mock_agent.config.model = "gpt-4o"
        mock_agent.config.tools = []
        mock_agent.config.system_prompt = ""
        mock_agent.config.strategy = None
        mock_agent.config.memory = None
        mock_cls.return_value = mock_agent

        spec = load_workflow_string(MINIMAL_WORKFLOW)
        # Since we mocked UniversalAgent, workflow is the mock
        result = await spec.run("test message")
        assert result == "result"


# ---------------------------------------------------------------------------
# Loop stop condition
# ---------------------------------------------------------------------------


class TestLoopStopCondition:
    def test_stop_condition_parsed(self):
        spec = load_workflow_string(LOOP_WORKFLOW)
        assert isinstance(spec.workflow, LoopAgent)
        # The should_stop function should be set
        assert spec.workflow._should_stop is not None
        # Test the stop function
        assert spec.workflow._should_stop("DONE: all good", 0) is True
        assert spec.workflow._should_stop("still working", 0) is False

    def test_no_stop_condition(self):
        yaml_str = """
name: no-stop-test
agents:
  refiner:
    model: gpt-4o
workflow:
  type: loop
  agent: refiner
  max_iterations: 5
"""
        spec = load_workflow_string(yaml_str)
        assert isinstance(spec.workflow, LoopAgent)
        assert spec.workflow._should_stop is None
        assert spec.workflow.config.max_iterations == 5
