# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM Harness — smart proxy for AI coding tools.

Routes LLM requests from tools like Claude Code, Cursor, and Copilot
to the right model based on task complexity, enforces budget policies,
and tracks spend per user/team/project.

Usage::

    from sagewai.harness import (
        RequestClassifier,
        HarnessRouter,
        PolicyEngine,
        HarnessProxy,
        ComplexityTier,
        ModelTierConfig,
    )
"""

from sagewai.harness.agent import HarnessingAgent, register_harness_directives
from sagewai.harness.backend import (
    AnthropicBackend,
    LiteLLMProxyBackend,
    OpenAIBackend,
)
from sagewai.harness.budget import HarnessBudgetManager, HarnessBudgetResult
from sagewai.harness.discovery import (
    DiscoveredServer,
    build_local_backends,
    discover_local_backends,
)
from sagewai.harness.classifier import (
    ClassificationResult,
    ClassifierThresholds,
    ComplexityTier,
    RequestClassifier,
)
from sagewai.harness.models import (
    HarnessAuditEvent,
    HarnessConfig,
    HarnessIdentity,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
    RoutingDecision,
    SpendRecord,
)
from sagewai.harness.middleware import HarnessMiddleware, harness_wrap
from sagewai.harness.policy import PolicyEngine
from sagewai.harness.proxy import HarnessProxy
from sagewai.harness.router import HarnessRouter
from sagewai.harness.store import InMemoryHarnessStore

__all__ = [
    "AnthropicBackend",
    "ClassificationResult",
    "ClassifierThresholds",
    "ComplexityTier",
    "HarnessAuditEvent",
    "HarnessBudgetManager",
    "HarnessBudgetResult",
    "HarnessConfig",
    "HarnessIdentity",
    "HarnessKey",
    "HarnessMiddleware",
    "HarnessProxy",
    "HarnessRouter",
    "HarnessingAgent",
    "InMemoryHarnessStore",
    "LiteLLMProxyBackend",
    "ModelTierConfig",
    "OpenAIBackend",
    "PolicyEngine",
    "PolicyRule",
    "PolicyScope",
    "RequestClassifier",
    "DiscoveredServer",
    "RoutingDecision",
    "SpendRecord",
    "build_local_backends",
    "discover_local_backends",
    "harness_wrap",
    "register_harness_directives",
]
