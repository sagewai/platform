#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 30 — On-call agent: the v1.0 lighthouse demo.

**Freemium boundary:** running this lighthouse against a real on-call
goal in production goes through the hosted ``sagewai-llm`` service
(default: ``api.sagewai.ai``) or a local copy of the
``sagewai/sagewai-llm`` repo on ``127.0.0.1:8100`` for blueprint
generation. The example as written drives a hand-built blueprint with
mocked tools so you can run it offline. The other 32 examples in this
directory run with no hosted service — pure OSS path.

The autopilot's lighthouse use case. A synthetic PagerDuty alert fires;
an autopilot mission triages it by:

1. Reading recent metrics from a (mocked) observability stack
2. Running a runbook in a (mocked) sandbox: ``top``, ``ps``, ``uptime``
3. Drafting a first response with the model's analysis
4. Posting the response to a (mocked) Slack channel

All four tools are registered with the new :class:`ToolRegistry` and
called by the agent autonomously between LLM turns. The mocks here are
in-process functions so the example runs without any infrastructure;
swap them for real implementations (PagerDuty webhook, Prometheus query,
Docker sandbox, Slack webhook) in production.

This is the exact mission run for the launch tutorial video. It is also
the dogfood scenario Arda runs against synthetic incidents during the
v1.0 acceptance gates.

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY
    python 30_oncall_agent.py

To run end-to-end without an LLM key, the example detects no key set
and prints what would happen, including the resolved tool calls.

Real-world use cases:

- Senior SRE at a 200-person fintech SaaS — your three-person on-call
  rotation is paged at 02:47am for an api-gateway 5xx spike. You want
  the agent to pull the 15-minute metric window, run ``ps``/``top`` in
  a sandbox, and post the first-pass diagnosis to #incidents before
  the human eyes finish typing in their password.
- Engineering manager at a 150-person devtools company — your team
  rotates on-call across six engineers. Half of them are uncomfortable
  triaging the database tier. The agent's drafted response keeps the
  page actionable for the on-call engineer who didn't write the
  service.
- Platform-team lead at a 400-person e-commerce SaaS — Black Friday
  is six weeks away. You're staffing the war-room rotation and want
  every page to land with metrics already pulled, runbook already
  attempted, and a one-paragraph summary in Slack. Agent runs first,
  human runs second.
- Senior backend engineer at a 100-person AI-feature startup —
  founders' rule is "engineers triage their own services". The agent
  is the first review of the page so engineers get an evidence packet
  before they answer. Cuts mean-time-to-acknowledge by half.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sagewai.autopilot._types import AgentKind, MissionState, Mode
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.controller.executor import ExecutorConfig
from sagewai.autopilot.controller.tool_registry import ToolRegistry
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.models import EvalRef, Metric, ProviderRequirement


# ── synthetic incident data ─────────────────────────────────────────


@dataclass
class SyntheticIncident:
    """A pretend PagerDuty alert that came in at 02:47am."""

    incident_id: str = "P-2026-05-01-001"
    service: str = "api-gateway"
    summary: str = "5xx error rate spiked to 12% over the last 5 min"
    severity: str = "high"
    fired_at: str = "2026-05-01T02:47:00Z"


_INCIDENT = SyntheticIncident()


# ── tool implementations (mocks for the example) ────────────────────


async def fetch_recent_metrics(service: str, lookback_minutes: int = 15) -> dict:
    """Pretend to query VictoriaMetrics for recent metrics on `service`."""
    return {
        "service": service,
        "lookback_minutes": lookback_minutes,
        "metrics": {
            "http_5xx_rate_pct": [0.4, 0.5, 0.4, 8.1, 12.3, 11.7],
            "http_p99_latency_ms": [120, 124, 119, 480, 890, 1100],
            "active_connections": [340, 350, 345, 510, 620, 680],
        },
        "timestamps": [
            "02:32", "02:35", "02:38", "02:41", "02:44", "02:47",
        ],
        "note": "5xx rate jumped sharply at ~02:41; p99 latency followed; connection pool grew",
    }


async def run_runbook_command(command: str) -> dict:
    """Pretend to run a shell command in a sandbox and return its output."""
    canned = {
        "uptime": "02:48:12 up 14 days, 6:02, load average: 8.42, 7.91, 6.13",
        "top -bn1 | head -20": (
            "Tasks: 312 total, 1 running, 311 sleeping\n"
            "%Cpu(s): 78.2 us, 12.4 sy, 0.0 ni, 9.4 id\n"
            "MiB Mem: 16384 total, 1242 free, 14512 used"
        ),
        "ps aux --sort=-%cpu | head -5": (
            "USER  PID %CPU %MEM COMMAND\n"
            "app   8421 65.2 41.3 node /app/server.js\n"
            "app   8422 12.8 14.1 node /app/worker.js\n"
            "app   8423  4.1  2.3 nginx: master process"
        ),
    }
    return {
        "command": command,
        "exit_code": 0,
        "stdout": canned.get(command, f"(simulated output for: {command})"),
        "duration_ms": 38,
    }


async def post_to_slack(channel: str, message: str) -> dict:
    """Pretend to POST to a Slack webhook."""
    print(f"\n  📨 Slack [#{channel}]:\n  {message.replace(chr(10), chr(10) + '  ')}\n")
    return {
        "channel": channel,
        "posted_at": datetime.now(tz=timezone.utc).isoformat(),
        "ok": True,
    }


async def acknowledge_pagerduty(incident_id: str, note: str) -> dict:
    """Pretend to ack the PagerDuty incident."""
    return {
        "incident_id": incident_id,
        "acknowledged_at": datetime.now(tz=timezone.utc).isoformat(),
        "note": note,
    }


# ── tool registry ──────────────────────────────────────────────────


def _build_tool_registry() -> ToolRegistry:
    """Register the four lighthouse tools with their JSON schemas."""
    registry = ToolRegistry()

    registry.register(
        name="fetch_recent_metrics",
        description=(
            "Query the observability stack for recent metrics on a service. "
            "Returns 5xx rate, p99 latency, and active connections over the lookback window."
        ),
        parameters={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. 'api-gateway')",
                },
                "lookback_minutes": {
                    "type": "integer",
                    "description": "How far back to query, in minutes",
                    "default": 15,
                },
            },
            "required": ["service"],
        },
        callable_=fetch_recent_metrics,
    )

    registry.register(
        name="run_runbook_command",
        description=(
            "Run a shell command in the on-call sandbox and return stdout. "
            "Use this to inspect process state, system load, or run diagnostic commands."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run (e.g. 'uptime', 'top -bn1 | head -20')",
                },
            },
            "required": ["command"],
        },
        callable_=run_runbook_command,
    )

    registry.register(
        name="post_to_slack",
        description=(
            "Post a message to a Slack channel. Use this to share triage findings "
            "and escalation notices with the on-call team."
        ),
        parameters={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Slack channel name without the '#'",
                },
                "message": {
                    "type": "string",
                    "description": "Message body — markdown allowed",
                },
            },
            "required": ["channel", "message"],
        },
        callable_=post_to_slack,
    )

    registry.register(
        name="acknowledge_pagerduty",
        description=(
            "Acknowledge the PagerDuty incident so the on-call rotation isn't paged "
            "repeatedly while triage is in progress."
        ),
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "PagerDuty incident ID",
                },
                "note": {
                    "type": "string",
                    "description": "Note to attach to the ack",
                },
            },
            "required": ["incident_id", "note"],
        },
        callable_=acknowledge_pagerduty,
    )

    return registry


# ── mission construction ──────────────────────────────────────────


def _build_oncall_blueprint() -> Blueprint:
    """Build a single-step LLM agent that has access to all four tools."""
    triage_agent = Agent(
        id="triage",
        kind=AgentKind.LLM,
        prompt_ref="prompts/oncall-triage.md",  # executor falls back to a generic prompt if file is missing
        tools=(
            "fetch_recent_metrics",
            "run_runbook_command",
            "post_to_slack",
            "acknowledge_pagerduty",
        ),
    )
    graph = AgentGraph(entry="triage", nodes=(triage_agent,), edges=())
    return Blueprint(
        id="bp-oncall-triage",
        version="1",
        title="On-call triage",
        description="Triage an incoming PagerDuty alert and post first response to Slack.",
        category="oncall",
        mode=Mode.EVENT_DRIVEN,
        example_goals=(
            "Triage incoming PagerDuty alerts and post a first response to Slack.",
        ),
        required_slots={},
        optional_slots={},
        tools_required=(
            "fetch_recent_metrics",
            "run_runbook_command",
            "post_to_slack",
            "acknowledge_pagerduty",
        ),
        providers_required=(
            ProviderRequirement(role="triage", capability="reasoning", tier="medium"),
        ),
        agent_graph=graph,
        success_criteria=EvalRef(
            dataset_id="oncall_triage_eval",
            metrics=(Metric(name="response_quality", op=">=", value=4.0),),
        ),
    )


def _build_mission(blueprint: Blueprint, incident: SyntheticIncident) -> Mission:
    """Build a Mission carrying the synthetic incident in its slots."""
    mission = Mission(
        mission_id=f"oncall-{incident.incident_id}",
        blueprint_id=blueprint.id,
        project_id="oncall-demo",
        blueprint_version=blueprint.version,
        slots={
            "incident_id": incident.incident_id,
            "service": incident.service,
            "summary": incident.summary,
            "severity": incident.severity,
            "fired_at": incident.fired_at,
            "__blueprint_json__": blueprint.model_dump_json(),
        },
    )
    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)
    return mission


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    """Run the on-call triage mission end-to-end."""
    print("─" * 72)
    print(" Sagewai on-call agent — v1.0 lighthouse demo")
    print("─" * 72)
    print()
    print(f"  Synthetic incident received:")
    print(f"    id:       {_INCIDENT.incident_id}")
    print(f"    service:  {_INCIDENT.service}")
    print(f"    summary:  {_INCIDENT.summary}")
    print(f"    severity: {_INCIDENT.severity}")
    print(f"    fired:    {_INCIDENT.fired_at}")
    print()

    has_llm_key = bool(
        os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not has_llm_key:
        print(
            "  ⚠ No OPENAI_API_KEY or ANTHROPIC_API_KEY set.\n"
            "    The mission will run but the LLM agent will be skipped\n"
            "    (no tool calls executed).\n"
            "    Set a key to see the full triage flow.\n"
        )

    registry = _build_tool_registry()
    blueprint = _build_oncall_blueprint()
    mission = _build_mission(blueprint, _INCIDENT)

    cfg = ExecutorConfig(
        model="gpt-4o-mini" if os.environ.get("OPENAI_API_KEY") else "claude-haiku-4-5-20251001",
        max_tool_iterations=6,
        tool_registry=registry,
    )
    driver = MissionDriver(executor_config=cfg)

    print("  Triage mission running…\n")
    result = await driver.execute(mission)

    print(f"  ✓ Mission status: {result.status}  ({result.duration_seconds:.2f}s)")
    print()
    for step in result.steps:
        print(f"  Step {step.node_id}:  status={step.status}")
        if step.tool_calls:
            print(f"    tool_calls: {list(step.tool_calls)}")
        if step.output_preview:
            preview = step.output_preview[:300]
            print(f"    response preview: {preview}")
            if step.output and len(step.output) > len(preview):
                print(f"    (full output {len(step.output)} chars — truncated)")
        if step.telemetry:
            t = step.telemetry
            print(
                f"    telemetry: model={t.model_used} "
                f"in={t.input_tokens}t out={t.output_tokens}t "
                f"cost=${t.cost_usd:.4f} latency={t.latency_ms:.0f}ms"
            )
        print()

    print("─" * 72)
    print(
        "  In v1.1: a second incident fires next week. Curator has fine-tuned a\n"
        "  triage model from this run; the second mission runs against the\n"
        "  Curator-promoted local model and costs measurably less.\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
