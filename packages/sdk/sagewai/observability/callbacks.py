# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LiteLLM callback integration for automatic OTel spans.

Registers global LiteLLM success/failure callbacks that create OpenTelemetry
spans for every LiteLLM call — useful when app code calls ``litellm.acompletion``
directly outside of the agent framework.

Usage::

    from sagewai.observability.callbacks import setup_litellm_callbacks

    setup_litellm_callbacks()  # call once at startup
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.observability.tracing import _NoOpTracer, get_tracer, is_tracing_enabled

logger = logging.getLogger(__name__)

_callbacks_installed = False


def setup_litellm_callbacks(*, otel: bool = True) -> bool:
    """Register LiteLLM global callbacks for observability.

    Args:
        otel: Whether to install OTel span callbacks (default True).

    Returns:
        True if callbacks were successfully registered.
    """
    global _callbacks_installed
    if _callbacks_installed:
        return True

    try:
        import litellm
    except ImportError:
        logger.debug("litellm not installed, skipping callback registration")
        return False

    handlers: list[Any] = []
    if otel:
        handlers.append(_OTelLiteLLMHandler())

    if handlers:
        litellm.success_callback = handlers
        litellm.failure_callback = handlers
        _callbacks_installed = True
        logger.info("LiteLLM callbacks registered: otel=%s", otel)
        return True

    return False


class _OTelLiteLLMHandler:
    """LiteLLM callback handler that creates OTel spans."""

    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any):
        """Called by LiteLLM on successful completion."""
        if not is_tracing_enabled():
            return

        tracer = get_tracer()
        if isinstance(tracer, _NoOpTracer):
            return

        model = kwargs.get("model", "unknown")
        with tracer.start_as_current_span(f"litellm.call.{model}") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.status", "success")
            if hasattr(response_obj, "usage") and response_obj.usage:
                span.set_attribute(
                    "llm.input_tokens",
                    getattr(response_obj.usage, "prompt_tokens", 0) or 0,
                )
                span.set_attribute(
                    "llm.output_tokens",
                    getattr(response_obj.usage, "completion_tokens", 0) or 0,
                )

    def log_failure_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ):
        """Called by LiteLLM on failed completion."""
        if not is_tracing_enabled():
            return

        tracer = get_tracer()
        if isinstance(tracer, _NoOpTracer):
            return

        model = kwargs.get("model", "unknown")
        with tracer.start_as_current_span(f"litellm.call.{model}") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.status", "error")
            exception = kwargs.get("exception", None)
            if exception:
                span.set_attribute("llm.error", str(exception))
