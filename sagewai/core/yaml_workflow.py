# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""YAML workflow specification parser for declarative multi-agent DAGs.

Define multi-agent workflows in YAML and compile them into executable
workflow agents using the existing :class:`SequentialAgent`,
:class:`ParallelAgent`, and :class:`LoopAgent` patterns.

Example YAML::

    name: research-pipeline
    description: Research and summarize a topic

    agents:
      researcher:
        model: gpt-4o
        system_prompt: You are a research specialist.
        max_iterations: 5

      writer:
        model: gpt-4o
        system_prompt: You are a professional writer.

      reviewer:
        model: gpt-4o
        system_prompt: You review and improve text quality.

    workflow:
      type: sequential
      steps:
        - agent: researcher
        - type: parallel
          agents: [writer, reviewer]
        - agent: reviewer

Usage::

    from sagewai.core.yaml_workflow import load_workflow

    pipeline = load_workflow("workflow.yaml")
    result = await pipeline.chat("Research quantum computing")
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml

from sagewai.core.base import BaseAgent
from sagewai.core.workflows import (
    ConditionalAgent,
    LoopAgent,
    ParallelAgent,
    SequentialAgent,
)
from sagewai.engines.universal import UniversalAgent

logger = logging.getLogger(__name__)


class WorkflowSpec:
    """Parsed workflow specification from YAML.

    Holds the workflow metadata, agent definitions, and the compiled
    workflow agent ready for execution.
    """

    def __init__(
        self,
        name: str,
        description: str,
        agents: dict[str, BaseAgent],
        workflow: BaseAgent,
    ) -> None:
        self.name = name
        self.description = description
        self.agents = agents
        self.workflow = workflow

    async def run(self, message: str) -> str:
        """Execute the workflow with a user message."""
        return await self.workflow.chat(message)


class WorkflowParseError(Exception):
    """Raised when a YAML workflow spec is invalid."""


def load_workflow(path: str | Path) -> WorkflowSpec:
    """Load and compile a workflow from a YAML file.

    Args:
        path: Path to the YAML workflow file.

    Returns:
        A compiled WorkflowSpec ready for execution.

    Raises:
        WorkflowParseError: If the YAML is invalid or has structural issues.
        FileNotFoundError: If the file doesn't exist.
    """
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    return parse_workflow(data)


def load_workflow_string(
    yaml_string: str,
    agent_resolver: Any | None = None,
) -> WorkflowSpec:
    """Parse and compile a workflow from a YAML string.

    Args:
        yaml_string: YAML workflow specification as a string.
        agent_resolver: Optional callable ``(name: str) -> BaseAgent | None``
            for resolving ``ref:`` agent definitions.

    Returns:
        A compiled WorkflowSpec ready for execution.
    """
    data = yaml.safe_load(yaml_string)
    return parse_workflow(data, agent_resolver=agent_resolver)


def parse_workflow(
    data: dict[str, Any],
    agent_resolver: Any | None = None,
) -> WorkflowSpec:
    """Parse and compile a workflow from a dict (parsed YAML).

    Args:
        data: Dict from yaml.safe_load.
        agent_resolver: Optional callable ``(name: str) -> BaseAgent | None``
            used to resolve ``ref:`` agent definitions that reference
            existing agents (e.g. from the playground factory).

    Returns:
        A compiled WorkflowSpec.

    Raises:
        WorkflowParseError: If the specification is invalid.
    """
    if not isinstance(data, dict):
        raise WorkflowParseError("Workflow spec must be a YAML mapping")

    name = data.get("name")
    if not name:
        raise WorkflowParseError("Workflow must have a 'name' field")

    description = data.get("description", "")

    # Workflow-level defaults (applied to inline agents without explicit overrides)
    default_model = data.get("default_model")
    default_fallbacks = data.get("fallback_models", [])

    # Parse agent definitions
    agent_defs = data.get("agents", {})
    if not isinstance(agent_defs, dict):
        raise WorkflowParseError("'agents' must be a mapping")

    agents: dict[str, BaseAgent] = {}
    for agent_name, agent_config in agent_defs.items():
        agents[agent_name] = _build_agent(
            agent_name,
            agent_config,
            agent_resolver,
            default_model=default_model,
            default_fallbacks=default_fallbacks,
        )

    # Parse workflow DAG
    workflow_def = data.get("workflow")
    if workflow_def is None:
        raise WorkflowParseError("Workflow must have a 'workflow' field")

    workflow_agent = _build_workflow_node(name, workflow_def, agents)

    return WorkflowSpec(
        name=name,
        description=description,
        agents=agents,
        workflow=workflow_agent,
    )


def _build_agent(
    name: str,
    config: dict[str, Any] | None,
    agent_resolver: Any | None = None,
    *,
    default_model: str | None = None,
    default_fallbacks: list[str] | None = None,
) -> BaseAgent:
    """Build a UniversalAgent from an agent definition.

    If the config contains a ``ref`` key, resolve the agent from the
    registry via *agent_resolver* instead of creating a new one.

    Workflow-level *default_model* and *default_fallbacks* are applied
    to inline agents that don't specify their own values.
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise WorkflowParseError(f"Agent '{name}' config must be a mapping, got {type(config)}")

    # Resolve reference to an existing registered agent
    ref = config.get("ref")
    if ref:
        if agent_resolver is None:
            raise WorkflowParseError(
                f"Agent '{name}' uses ref: '{ref}' but no agent registry is available"
            )
        resolved = agent_resolver(ref)
        if resolved is None:
            raise WorkflowParseError(
                f"Agent '{name}' references '{ref}' which is not in the registry"
            )
        logger.info("Resolved ref '%s' for workflow agent '%s'", ref, name)

        # Apply YAML-level overrides to the resolved agent if specified
        override_prompt = config.get("system_prompt")
        if override_prompt:
            resolved.config.system_prompt = override_prompt
        override_model = config.get("model")
        if override_model:
            resolved.config.model = override_model
        if config.get("temperature") is not None:
            resolved.config.inference.temperature = config["temperature"]
        if config.get("max_tokens") is not None:
            resolved.config.inference.max_tokens = config["max_tokens"]
        if config.get("max_iterations") is not None:
            resolved.config.max_iterations = config["max_iterations"]
        if default_fallbacks:
            resolved.config.inference.fallback_models = list(default_fallbacks)

        # Use the workflow step name, not the original agent name
        resolved.config.name = name
        return resolved

    # Model: explicit > workflow default > gpt-4o
    model = config.get("model") or default_model or "gpt-4o"

    # Fallback models: explicit > workflow default
    fallback_models = config.get("fallback_models") or default_fallbacks or []

    agent = UniversalAgent(
        name=name,
        model=model,
        system_prompt=config.get("system_prompt", ""),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens"),
        max_iterations=config.get("max_iterations", 10),
    )

    # Pass api_base for models that need custom endpoints (e.g. LM Studio)
    if config.get("api_base"):
        agent.config.inference.api_base = config["api_base"]

    # Apply fallback models to the agent's inference params
    if fallback_models:
        agent.config.inference.fallback_models = list(fallback_models)

    return agent


def _build_workflow_node(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> BaseAgent:
    """Recursively build a workflow node from its YAML definition.

    A node can be:
    - A reference to a named agent: ``{"agent": "researcher"}``
    - A sequential workflow: ``{"type": "sequential", "steps": [...]}``
    - A parallel workflow: ``{"type": "parallel", "agents": [...]}``
    - A loop workflow: ``{"type": "loop", "agent": "...", ...}``
    - A conditional: ``{"type": "conditional", "condition": {...}, ...}``
    - An LLM router: ``{"type": "router", "model": "...", ...}``
    """
    if not isinstance(node, dict):
        raise WorkflowParseError(
            f"Workflow node must be a mapping, got {type(node)}"
        )

    # Simple agent reference
    if "agent" in node and "type" not in node:
        agent_name = node["agent"]
        if agent_name not in agents:
            raise WorkflowParseError(
                f"Unknown agent '{agent_name}' referenced in workflow"
            )
        return agents[agent_name]

    node_type = node.get("type")
    if node_type is None:
        raise WorkflowParseError(
            "Workflow node must have 'type' "
            "(sequential/parallel/loop/conditional/router) "
            "or 'agent' reference"
        )

    if node_type == "sequential":
        return _build_sequential(parent_name, node, agents)
    elif node_type == "parallel":
        return _build_parallel(parent_name, node, agents)
    elif node_type == "loop":
        return _build_loop(parent_name, node, agents)
    elif node_type == "conditional":
        return _build_conditional(parent_name, node, agents)
    elif node_type == "router":
        return _build_router(parent_name, node, agents)
    else:
        raise WorkflowParseError(
            f"Unknown workflow type: '{node_type}'"
        )


def _build_sequential(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> SequentialAgent:
    """Build a SequentialAgent from a workflow node."""
    steps = node.get("steps", [])
    if not steps:
        raise WorkflowParseError("Sequential workflow must have at least one step")

    sub_agents = []
    for i, step in enumerate(steps):
        step_name = f"{parent_name}-step-{i}"
        sub_agents.append(_build_workflow_node(step_name, step, agents))

    return SequentialAgent(
        name=node.get("name", f"{parent_name}-sequential"),
        agents=sub_agents,
    )


def _build_parallel(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> ParallelAgent:
    """Build a ParallelAgent from a workflow node."""
    agent_refs = node.get("agents", [])
    if not agent_refs:
        raise WorkflowParseError("Parallel workflow must have at least one agent")

    sub_agents = []
    for ref in agent_refs:
        if isinstance(ref, str):
            if ref not in agents:
                raise WorkflowParseError(f"Unknown agent '{ref}' in parallel block")
            sub_agents.append(agents[ref])
        elif isinstance(ref, dict):
            sub_agents.append(_build_workflow_node(f"{parent_name}-parallel", ref, agents))
        else:
            raise WorkflowParseError(f"Invalid agent reference in parallel block: {ref}")

    return ParallelAgent(
        name=node.get("name", f"{parent_name}-parallel"),
        agents=sub_agents,
    )


def _build_loop(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> LoopAgent:
    """Build a LoopAgent from a workflow node."""
    agent_ref = node.get("agent")
    if agent_ref is None:
        raise WorkflowParseError("Loop workflow must specify an 'agent'")

    if isinstance(agent_ref, str):
        if agent_ref not in agents:
            raise WorkflowParseError(f"Unknown agent '{agent_ref}' in loop block")
        sub_agent = agents[agent_ref]
    elif isinstance(agent_ref, dict):
        sub_agent = _build_workflow_node(f"{parent_name}-loop", agent_ref, agents)
    else:
        raise WorkflowParseError(f"Invalid agent reference in loop block: {agent_ref}")

    max_iter = node.get("max_iterations", 3)
    stop_condition = node.get("stop_condition")

    should_stop = None
    if stop_condition:

        def should_stop(result: str, _i: int, kw: str = stop_condition) -> bool:
            return kw in result

    return LoopAgent(
        name=node.get("name", f"{parent_name}-loop"),
        agent=sub_agent,
        max_iterations=max_iter,
        should_stop=should_stop,
    )


# ---------------------------------------------------------------------------
# Conditional / Router node builders
# ---------------------------------------------------------------------------


def _build_condition_func(
    condition: dict[str, Any],
) -> Callable[[str], str]:
    """Build a sync condition function from a YAML condition spec.

    Supported keys (mutually exclusive):

    - ``contains`` — case-insensitive substring check
    - ``regex`` — regex search (first match group or full match)
    - ``equals`` — exact string equality
    """
    if "contains" in condition:
        keyword = str(condition["contains"]).lower()

        def _contains(text: str) -> str:
            return "then" if keyword in text.lower() else "else"

        return _contains

    if "regex" in condition:
        pattern = re.compile(condition["regex"])

        def _regex(text: str) -> str:
            return "then" if pattern.search(text) else "else"

        return _regex

    if "equals" in condition:
        expected = str(condition["equals"])

        def _equals(text: str) -> str:
            return "then" if text.strip() == expected else "else"

        return _equals

    raise WorkflowParseError(
        "Conditional 'condition' must specify one of: "
        "contains, regex, equals"
    )


def _build_conditional(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> ConditionalAgent:
    """Build a ConditionalAgent from a ``type: conditional`` node.

    Expected YAML shape::

        type: conditional
        condition:
          contains: "error"   # or regex / equals
        then:
          agent: error_handler
        else:
          agent: normal_handler
    """
    cond_spec = node.get("condition")
    if not isinstance(cond_spec, dict):
        raise WorkflowParseError(
            "Conditional node must have a 'condition' mapping "
            "(contains, regex, or equals)"
        )

    condition_fn = _build_condition_func(cond_spec)

    then_node = node.get("then")
    else_node = node.get("else")

    if then_node is None:
        raise WorkflowParseError(
            "Conditional node must have a 'then' branch"
        )

    then_agent = _build_workflow_node(
        f"{parent_name}-then", then_node, agents
    )

    branches: dict[str, BaseAgent] = {"then": then_agent}
    default_branch: BaseAgent | None = None

    if else_node is not None:
        else_agent = _build_workflow_node(
            f"{parent_name}-else", else_node, agents
        )
        branches["else"] = else_agent
        default_branch = else_agent

    return ConditionalAgent(
        name=node.get("name", f"{parent_name}-conditional"),
        condition=condition_fn,
        branches=branches,
        default_branch=default_branch,
    )


def _build_llm_router_condition(
    model: str,
    prompt: str,
    route_keys: list[str],
) -> Callable[[str], Awaitable[str]]:
    """Build an async LLM-based routing condition.

    The returned coroutine function calls ``litellm.acompletion`` with
    *temperature=0* and instructs the model to respond with exactly one
    of the *route_keys*.  On failure it falls back to a simple keyword
    match so the workflow never crashes due to a classification hiccup.
    """

    async def condition(input_text: str) -> str:
        try:
            import litellm  # noqa: WPS433

            full_prompt = (
                f"{prompt}\n\n"
                f"Input: {input_text}\n\n"
                f"Respond with EXACTLY one of: "
                f"{', '.join(route_keys)}"
            )
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=0.0,
                max_tokens=20,
            )
            result = (
                (response.choices[0].message.content or "")
                .strip()
                .lower()
            )
            # Find best match among route keys
            for key in route_keys:
                if key.lower() in result:
                    return key
            return route_keys[0]
        except Exception:
            # Fallback: simple keyword match
            input_lower = input_text.lower()
            for key in route_keys:
                if key.lower() in input_lower:
                    return key
            return route_keys[0] if route_keys else "default"

    return condition


def _build_router(
    parent_name: str,
    node: dict[str, Any],
    agents: dict[str, BaseAgent],
) -> ConditionalAgent:
    """Build a ConditionalAgent from a ``type: router`` node.

    Expected YAML shape::

        type: router
        model: gpt-4o-mini
        prompt: "Classify as: technical, billing, general"
        routes:
          technical:
            agent: tech_agent
          billing:
            agent: billing_agent
          general:
            agent: general_agent
    """
    model = node.get("model", "gpt-4o-mini")
    prompt = node.get("prompt")
    if not prompt:
        raise WorkflowParseError(
            "Router node must have a 'prompt' field"
        )

    routes_spec = node.get("routes")
    if not isinstance(routes_spec, dict) or not routes_spec:
        raise WorkflowParseError(
            "Router node must have a non-empty 'routes' mapping"
        )

    branches: dict[str, BaseAgent] = {}
    for route_key, route_node in routes_spec.items():
        branches[route_key] = _build_workflow_node(
            f"{parent_name}-route-{route_key}", route_node, agents
        )

    route_keys = list(branches.keys())
    condition_fn = _build_llm_router_condition(
        model, prompt, route_keys
    )

    # First route is the default fallback
    default_branch = branches[route_keys[0]]

    return ConditionalAgent(
        name=node.get("name", f"{parent_name}-router"),
        condition=condition_fn,
        branches=branches,
        default_branch=default_branch,
    )
