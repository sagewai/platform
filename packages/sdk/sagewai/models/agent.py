# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Agent configuration model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from sagewai.models.inference import InferenceParams, InferencePreset
from sagewai.models.tool import ToolSpec


class AgentConfig(BaseModel):
    """Configuration for an agent instance.

    Quick start — only ``name`` is required::

        AgentConfig(name="my-agent")

    With a specific model::

        AgentConfig(name="my-agent", model="claude-sonnet-4-20250514")

    With safety guardrails::

        AgentConfig(name="my-agent", guardrails=[PIIGuard()])

    Inference parameters can be set in three ways::

        # 1. Use a preset
        AgentConfig(name="bot", inference=InferencePreset.CREATIVE)

        # 2. Use full InferenceParams
        AgentConfig(name="bot", inference=InferenceParams(temperature=0.3, top_p=0.8))

        # 3. Backward-compatible shorthand (temperature/max_tokens at top level)
        AgentConfig(name="bot", temperature=0.5, max_tokens=2000)
    """

    name: str
    """Unique identifier for this agent. Used in logs, monitoring, and the admin panel."""

    model: str = "gpt-4o"
    """LLM model to use. Any LiteLLM-supported model string works.
    Examples: ``'gpt-4o'``, ``'claude-sonnet-4-20250514'``, ``'gemini/gemini-2.5-flash'``,
    ``'ollama/llama3'``"""

    system_prompt: str = ""
    """System prompt sent to the LLM on every call. Defines the agent's persona
    and behavior. Can be a plain string or a Jinja2 template."""

    tools: list[ToolSpec] = Field(default_factory=list)
    """List of tools available to the agent. Use the ``@tool`` decorator to create
    tools, or ``ToolSpec`` for manual definitions. Tools are invoked automatically
    during the agentic loop."""

    inference: InferenceParams = Field(default_factory=InferenceParams)
    """LLM inference parameters (temperature, top_p, max_tokens, etc.).
    Can also be set via a preset: ``inference=InferencePreset.CREATIVE``."""

    max_iterations: int = 10
    """Maximum number of tool-calling loop iterations before the agent stops.
    Prevents infinite loops. Increase for complex multi-step tasks."""

    strategy: Any = None
    """Execution strategy for the agentic loop. ``None`` defaults to ``ReActStrategy``.
    Options: ``ReActStrategy``, ``PlanningStrategy``, ``LATSStrategy``,
    ``TreeOfThoughtsStrategy``, ``SelfCorrectionStrategy``, ``RoutingStrategy``."""

    memory: Any = None
    """Memory provider for RAG-augmented conversations. ``None`` disables memory.
    Requires ``sagewai[memory]`` extras (pymilvus, nebula3-python)."""

    memory_top_k: int = 5
    """Number of results to retrieve from memory per query.
    Passed as ``top_k`` to the memory provider's ``retrieve()`` method."""

    auto_learn: bool = False
    """When ``True`` and ``memory`` is a ContextEngine, automatically extract
    key facts from conversations and store them as agent-scoped context.
    Extraction runs in the background every ``learn_every_n_turns`` turns."""

    learn_every_n_turns: int = 5
    """How often to run automatic memory extraction (every N chat turns).
    Only used when ``auto_learn=True``."""

    durability: str = "none"
    """Durability mode. ``'none'`` for ephemeral execution, ``'checkpoint'`` for
    durable execution with automatic checkpointing and resume support."""

    project_id: str | None = None
    """Project identifier for multi-tenant deployments. Scopes workflow runs,
    audit logs, and rate limits to this project."""

    directives: Any = None
    """Enable the Directive Engine for prompt preprocessing.

    - ``None`` or ``False``: Disabled (default).
    - ``True``: Auto-create a DirectiveEngine using the agent's existing
      context/memory/tools. Model profile is auto-detected from ``model``.
    - A ``DirectiveEngine`` instance: Use the provided engine directly.

    When enabled, user prompts are preprocessed to resolve ``@context``,
    ``@memory``, ``@agent``, ``/tool``, ``/mcp``, and ``#meta`` directives
    before the LLM call. This allows small/local models to leverage Sagewai's
    full infrastructure without native tool-calling support."""

    # --- Memory & Context (admin-configurable) ---

    context_scopes: list[str] = Field(
        default_factory=lambda: ["project"],
        description="Which context scopes this agent can access (org/project)",
    )
    """Which context scopes this agent can access.
    Valid values: ``'org'``, ``'project'``.
    Defaults to ``['project']``. Use tags for fine-grained filtering."""

    retrieval_config: dict[str, Any] = Field(
        default_factory=lambda: {"top_k": 5, "strategies": ["vector"], "reranking": False},
        description="Per-agent retrieval settings (top_k, strategies, reranking)",
    )
    """Per-agent retrieval settings. Keys:

    - ``top_k`` (int): Number of results to retrieve. Default 5.
    - ``strategies`` (list[str]): Retrieval strategies (``'vector'``, ``'bm25'``, ``'graph'``).
    - ``reranking`` (bool): Enable cross-encoder re-ranking."""

    directive_template: str = Field(
        default="",
        description="Directive template prepended to every prompt (supports sigil syntax)",
    )
    """Directive template prepended to every prompt. Supports sigil syntax
    (e.g. ``@context('topic')``, ``@memory('key')``). Empty string disables."""

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="before")
    @classmethod
    def _resolve_inference(cls, data: Any) -> Any:
        """Handle backward-compatible temperature/max_tokens and preset resolution."""
        if not isinstance(data, dict):
            return data

        inference = data.get("inference")

        # Resolve preset string or enum to InferenceParams
        if isinstance(inference, str):
            data["inference"] = InferenceParams.from_preset(InferencePreset(inference))
        elif isinstance(inference, InferencePreset):
            data["inference"] = InferenceParams.from_preset(inference)

        # Backward compatibility: top-level temperature/max_tokens → inference
        top_level_temp = data.pop("temperature", None)
        top_level_max = data.pop("max_tokens", None)

        if top_level_temp is not None or top_level_max is not None:
            inf = data.get("inference")
            if inf is None:
                inf = {}
            elif isinstance(inf, InferenceParams):
                inf = inf.model_dump(exclude_none=True)
            elif not isinstance(inf, dict):
                inf = {}

            if top_level_temp is not None:
                inf["temperature"] = top_level_temp
            if top_level_max is not None:
                inf["max_tokens"] = top_level_max
            data["inference"] = inf

        return data
