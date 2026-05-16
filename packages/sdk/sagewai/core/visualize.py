# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Workflow visualization — export workflows to Mermaid diagrams.

Converts YAML workflow definitions and execution state into
Mermaid flowchart diagrams for debugging and documentation.

Usage::

    from sagewai.core.visualize import workflow_to_mermaid

    # From YAML string
    mermaid = workflow_to_mermaid_from_yaml(yaml_string)
    print(mermaid)

    # From execution detail
    mermaid = execution_to_mermaid(execution_detail)
"""

from __future__ import annotations

from typing import Any


def workflow_to_mermaid(workflow_def: dict[str, Any]) -> str:
    """Convert a parsed YAML workflow definition to Mermaid flowchart.

    Args:
        workflow_def: Parsed YAML workflow dictionary.

    Returns:
        Mermaid diagram string.
    """
    lines = ["graph TD"]
    _node_counter = [0]

    def _next_id() -> str:
        _node_counter[0] += 1
        return f"N{_node_counter[0]}"

    def _add_node(
        node: dict[str, Any],
        parent_id: str | None = None,
    ) -> str:
        node_type = node.get("type", "sequential")
        node_id = _next_id()

        if node_type == "sequential":
            steps = node.get("steps", [])
            prev_id = parent_id
            first_id = None
            for step in steps:
                step_id = _add_node(step, prev_id)
                if first_id is None:
                    first_id = step_id
                if prev_id and prev_id != parent_id:
                    lines.append(f"    {prev_id} --> {step_id}")
                elif prev_id == parent_id and parent_id:
                    lines.append(f"    {parent_id} --> {step_id}")
                prev_id = step_id
            return first_id or node_id

        elif node_type == "parallel":
            fork_id = _next_id()
            join_id = _next_id()
            lines.append(f"    {fork_id}{{{{Fork}}}}")
            lines.append(f"    {join_id}{{{{Join}}}}")
            if parent_id:
                lines.append(f"    {parent_id} --> {fork_id}")
            for step in node.get("steps", []):
                step_id = _add_node(step)
                lines.append(f"    {fork_id} --> {step_id}")
                lines.append(f"    {step_id} --> {join_id}")
            return join_id

        elif node_type == "loop":
            loop_id = _next_id()
            agent_val = node.get("agent", "loop")
            name = (
                agent_val.get("name", "loop")
                if isinstance(agent_val, dict)
                else agent_val
            )
            max_iter = node.get("max_iterations", "?")
            lines.append(
                f'    {loop_id}[/"Loop: {name} (max={max_iter})"/]'
            )
            if parent_id:
                lines.append(f"    {parent_id} --> {loop_id}")
            lines.append(f"    {loop_id} -.->|repeat| {loop_id}")
            return loop_id

        elif node_type == "conditional":
            cond_id = _next_id()
            cond = node.get("condition", {})
            cond_text = ""
            if "contains" in cond:
                cond_text = f"contains '{cond['contains']}'"
            elif "regex" in cond:
                cond_text = f"regex '{cond['regex']}'"
            elif "equals" in cond:
                cond_text = f"equals '{cond['equals']}'"
            lines.append(
                f"    {cond_id}{{{cond_text or 'condition'}}}"
            )
            if parent_id:
                lines.append(f"    {parent_id} --> {cond_id}")
            then_node = node.get("then", {})
            if then_node:
                then_id = _add_node(then_node)
                lines.append(f"    {cond_id} -->|yes| {then_id}")
            else_node = node.get("else", {})
            if else_node:
                else_id = _add_node(else_node)
                lines.append(f"    {cond_id} -->|no| {else_id}")
            return cond_id

        elif node_type == "router":
            router_id = _next_id()
            prompt = node.get("prompt", "classify")[:30]
            lines.append(f"    {router_id}{{{prompt}}}")
            if parent_id:
                lines.append(f"    {parent_id} --> {router_id}")
            for route_name, route_node in node.get("routes", {}).items():
                route_id = _add_node(route_node)
                lines.append(
                    f"    {router_id} -->|{route_name}| {route_id}"
                )
            return router_id

        elif node_type == "approval":
            approval_id = _next_id()
            prompt = node.get("prompt", "Approval required")[:30]
            lines.append(f'    {approval_id}[/"{prompt}"/]')
            if parent_id:
                lines.append(f"    {parent_id} --> {approval_id}")
            return approval_id

        else:
            # Agent node (leaf)
            name = node.get("name") or node.get("agent", "agent")
            if isinstance(name, dict):
                name = name.get("name", "agent")
            lines.append(f"    {node_id}[{name}]")
            if parent_id:
                lines.append(f"    {parent_id} --> {node_id}")
            return node_id

    if "steps" in workflow_def:
        _add_node({"type": "sequential", "steps": workflow_def["steps"]})
    else:
        _add_node(workflow_def)

    return "\n".join(lines)


def workflow_to_mermaid_from_yaml(yaml_str: str) -> str:
    """Convert YAML workflow string to Mermaid diagram.

    Args:
        yaml_str: Raw YAML workflow definition.

    Returns:
        Mermaid diagram string.
    """
    import yaml

    workflow_def = yaml.safe_load(yaml_str)
    return workflow_to_mermaid(workflow_def)


def execution_to_mermaid(execution: Any) -> str:
    """Convert an ExecutionDetail to Mermaid with status coloring.

    Args:
        execution: ExecutionDetail from WorkflowMonitor.

    Returns:
        Mermaid diagram with status styling.
    """
    lines = ["graph TD"]
    style_lines: list[str] = []

    status_styles = {
        "completed": "fill:#4caf50,color:#fff",
        "running": "fill:#2196f3,color:#fff",
        "failed": "fill:#f44336,color:#fff",
        "pending": "fill:#9e9e9e,color:#fff",
        "waiting": "fill:#ff9800,color:#fff",
    }

    prev_id = None
    for i, step in enumerate(execution.steps):
        node_id = f"S{i}"
        duration = ""
        if step.duration_seconds:
            duration = f" ({step.duration_seconds:.1f}s)"
        label = f"{step.step_name}{duration}"
        lines.append(f"    {node_id}[{label}]")

        if prev_id:
            lines.append(f"    {prev_id} --> {node_id}")

        status = step.status.lower()
        if status in status_styles:
            style_lines.append(
                f"    style {node_id} {status_styles[status]}"
            )
        prev_id = node_id

    lines.extend(style_lines)
    return "\n".join(lines)
