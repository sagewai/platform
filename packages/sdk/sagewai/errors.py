# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unified error hierarchy for the Sagewai SDK.

All SDK-specific exceptions inherit from ``SagewaiError``, enabling a single
``except SagewaiError`` catch for any failure that originates from the SDK
while still allowing fine-grained handling of specific error categories.

Hierarchy::

    SagewaiError (base)
    +-- SagewaiLLMError            -- wraps all LLM provider failures
    |   +-- SagewaiRateLimitError  -- 429 / rate limit
    |   +-- SagewaiAuthError       -- invalid/missing API key
    |   +-- SagewaiModelNotFoundError -- model doesn't exist
    |   +-- SagewaiContextLengthError -- context window exceeded
    +-- SagewaiTimeoutError        -- step or workflow timeout
    +-- SagewaiConfigError         -- invalid config
    +-- SagewaiWorkflowError       -- workflow execution failures
    +-- SagewaiToolError           -- tool invocation failures
"""

from __future__ import annotations


class SagewaiError(Exception):
    """Base exception for all Sagewai SDK errors."""


class SagewaiLLMError(SagewaiError):
    """Wraps failures from LLM providers (OpenAI, Anthropic, Google, etc.).

    Attributes:
        provider: Name of the LLM provider (e.g. ``"openai"``, ``"google"``).
        model: Model identifier that was invoked (e.g. ``"gpt-4o"``).
        agent_name: Name of the agent that triggered the call.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        agent_name: str = "",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.agent_name = agent_name


class SagewaiRateLimitError(SagewaiLLMError):
    """Raised on HTTP 429 or provider-specific rate-limit signals.

    Attributes:
        retry_after: Seconds until the caller should retry (``None`` if unknown).
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        agent_name: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            message, provider=provider, model=model, agent_name=agent_name,
        )
        self.retry_after = retry_after


class SagewaiAuthError(SagewaiLLMError):
    """Raised when the API key is invalid or missing."""


class SagewaiModelNotFoundError(SagewaiLLMError):
    """Raised when the requested model does not exist."""


class SagewaiContextLengthError(SagewaiLLMError):
    """Raised when the prompt exceeds the model's context window."""


class SagewaiTimeoutError(SagewaiError):
    """Raised when a step or workflow exceeds its timeout."""


class SagewaiConfigError(SagewaiError):
    """Raised for invalid SDK or agent configuration."""


class SagewaiWorkflowError(SagewaiError):
    """Raised for workflow execution failures."""


class SagewaiToolError(SagewaiError):
    """Raised for tool invocation failures."""


class SagewaiContextError(SagewaiError):
    """Base class for context engine errors."""


class ContextIngestionError(SagewaiContextError):
    """Raised when document ingestion fails.

    Attributes:
        document_id: ID of the document that failed.
        stage: Pipeline stage that failed (parse, chunk, embed, store).
    """

    def __init__(
        self,
        message: str,
        *,
        document_id: str = "",
        stage: str = "",
    ) -> None:
        super().__init__(message)
        self.document_id = document_id
        self.stage = stage


class ContextSearchError(SagewaiContextError):
    """Raised when context search fails.

    Attributes:
        query: The search query that failed.
        strategies_attempted: Which strategies were tried.
    """

    def __init__(
        self,
        message: str,
        *,
        query: str = "",
        strategies_attempted: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.query = query
        self.strategies_attempted = strategies_attempted or []


class ContextDocumentNotFoundError(SagewaiContextError):
    """Raised when a document is not found by ID.

    Attributes:
        document_id: The ID that was looked up.
    """

    def __init__(self, document_id: str) -> None:
        super().__init__(f"Document not found: {document_id}")
        self.document_id = document_id


class SagewaiBudgetExceededError(SagewaiError):
    """Raised when an agent's budget limit has been exceeded and the action is ``stop``.

    Attributes:
        agent_name: Name of the agent that exceeded its budget.
        reason: Human-readable explanation of the budget violation.
    """

    def __init__(
        self,
        message: str,
        *,
        agent_name: str = "",
        reason: str = "",
    ) -> None:
        super().__init__(message)
        self.agent_name = agent_name
        self.reason = reason
