# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Token cost tracking per agent run.

Tracks input/output tokens and calculates costs using model pricing data.
Integrates with BaseAgent via event hooks to automatically track costs.

Usage::

    from sagewai.observability.costs import CostTracker

    tracker = CostTracker()
    agent.on_event(tracker.event_hook)
    await agent.chat("Hello")
    print(tracker.summary())
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (input, output) in USD.
# Source: public pricing pages as of 2025. Update as needed.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    # Google
    "gemini-2.5-flash": (0.10, 0.40),
    "gemini-2.5-flash-lite": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # Mistral
    "mistral-large-latest": (2.00, 6.00),
    "mistral-small-latest": (0.10, 0.30),
    # Meta (via providers)
    "llama-3.1-70b": (0.35, 0.40),
    "llama-3.1-8b": (0.05, 0.08),
}


def estimate_tokens_from_text(text: str) -> int:
    """Rough token estimate from text (4 chars ≈ 1 token)."""
    return max(1, len(text) // 4)


LOCAL_PROVIDERS = ("ollama", "lmstudio")


def is_local_model(model: str) -> bool:
    """Check if a model is a free local model (Ollama, LM Studio, etc.)."""
    lower = model.lower()
    for prefix in LOCAL_PROVIDERS:
        if lower.startswith(f"{prefix}/") or lower == prefix:
            return True
    return False


def get_model_pricing(model: str) -> tuple[float, float]:
    """Look up pricing for a model. Returns (input_per_1M, output_per_1M).

    Local models (Ollama, LM Studio) always return (0, 0).
    Falls back to a reasonable default if model is unknown.
    """
    # Local models are free
    if is_local_model(model):
        return (0.0, 0.0)

    # Exact match
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]

    # Prefix match (e.g., "gpt-4o-2024-08-06" → "gpt-4o")
    for key in MODEL_PRICING:
        if model.startswith(key):
            return MODEL_PRICING[key]

    # Default fallback
    return (1.00, 3.00)


def calculate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Calculate cost in USD for a single LLM call."""
    input_price, output_price = get_model_pricing(model)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


@dataclass
class LLMCallRecord:
    """Record of a single LLM call with token usage."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class RunSummary:
    """Summary of token usage and costs for an agent run."""

    agent_name: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: float = 0.0
    call_count: int = 0
    calls: list[LLMCallRecord] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def add_call(self, record: LLMCallRecord) -> None:
        """Add a call record to the summary."""
        self.calls.append(record)
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        self.total_cost_usd += record.cost_usd
        self.total_duration_ms += record.duration_ms
        self.call_count += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "agent_name": self.agent_name,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "call_count": self.call_count,
            "calls": [
                {
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cost_usd": round(c.cost_usd, 6),
                    "duration_ms": round(c.duration_ms, 2),
                }
                for c in self.calls
            ],
        }


class CostTracker:
    """Tracks token usage and costs across agent runs.

    Attach to an agent via its event hook system::

        tracker = CostTracker()
        agent.on_event(tracker.event_hook)
        await agent.chat("Hello")
        summary = tracker.current_run
    """

    def __init__(self) -> None:
        self._runs: list[RunSummary] = []
        self._current_run: RunSummary | None = None

    @property
    def current_run(self) -> RunSummary | None:
        """The currently active run summary."""
        return self._current_run

    @property
    def runs(self) -> list[RunSummary]:
        """All completed run summaries."""
        return list(self._runs)

    @property
    def total_cost(self) -> float:
        """Total cost across all runs."""
        return sum(r.total_cost_usd for r in self._runs)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all runs."""
        return sum(r.total_tokens for r in self._runs)

    def start_run(self, agent_name: str) -> RunSummary:
        """Start tracking a new agent run."""
        self._current_run = RunSummary(agent_name=agent_name)
        return self._current_run

    def end_run(self) -> RunSummary | None:
        """End the current run and store it."""
        if self._current_run is None:
            return None
        run = self._current_run
        self._runs.append(run)
        self._current_run = None
        return run

    def record_call(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float = 0.0,
    ) -> LLMCallRecord:
        """Record a single LLM call."""
        cost = calculate_cost(input_tokens, output_tokens, model)
        record = LLMCallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )
        if self._current_run:
            self._current_run.add_call(record)
        return record

    async def event_hook(self, event: Any, data: dict[str, Any]) -> None:
        """Event hook for BaseAgent integration.

        Attach via ``agent.on_event(tracker.event_hook)``.
        Listens for RUN_STARTED, LLM_CALL_FINISHED, and RUN_FINISHED events.
        """
        event_value = event.value if hasattr(event, "value") else str(event)

        if event_value == "run_started":
            agent_name = data.get("agent", "unknown")
            self.start_run(agent_name)
        elif event_value == "llm_call_finished":
            self.record_call(
                model=data.get("model", ""),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                duration_ms=data.get("duration_ms", 0.0),
            )
        elif event_value == "run_finished":
            self.end_run()

    def summary(self) -> dict[str, Any]:
        """Return a summary of all tracked runs."""
        return {
            "total_runs": len(self._runs),
            "total_cost_usd": round(self.total_cost, 6),
            "total_tokens": self.total_tokens,
            "runs": [r.to_dict() for r in self._runs],
        }

    def reset(self) -> None:
        """Clear all tracking data."""
        self._runs.clear()
        self._current_run = None
