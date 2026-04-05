# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai SDK - Enterprise Agentic Platform

A modular SDK for building domain-specific AI applications with MCP integration.
"""

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
from sagewai.core.agent_tool import agent_as_tool
from sagewai.core.base import BaseAgent
from sagewai.core.workflows import (
    ConditionalAgent,
    LoopAgent,
    ParallelAgent,
    SequentialAgent,
)
from sagewai.engines.google_native import GoogleNativeAgent
from sagewai.engines.universal import UniversalAgent

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
from sagewai.core.lats import LATSStrategy
from sagewai.core.planning import PlanningStrategy
from sagewai.core.routing import RoutingStrategy
from sagewai.core.self_correction import SelfCorrectionStrategy
from sagewai.core.strategies import ExecutionStrategy, ReActStrategy
from sagewai.core.tree_of_thoughts import TreeOfThoughtsStrategy

# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
from sagewai.safety.guardrails import (
    ContentFilter,
    Guardrail,
    GuardrailResult,
    GuardrailViolationError,
    OutputSchemaGuard,
    TokenBudgetGuard,
)
from sagewai.safety.hallucination import HallucinationGuard
from sagewai.safety.permissions import PermissionLevel, PermissionPolicy
from sagewai.safety.pii import PIIGuard

# ---------------------------------------------------------------------------
# Project Isolation
# ---------------------------------------------------------------------------
from sagewai.core.context import (
    ProjectContext,
    ProjectQuota,
    get_current_project,
    require_project,
    resolve_project_id,
)

# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
from sagewai.core.compactor import CompactionPipeline, RuleBasedCompactor
from sagewai.core.dlq import DeadLetterQueue
from sagewai.core.hooks import HookContext, HookResult, HookRunner
from sagewai.core.session_store import InMemorySessionStore, SessionCheckpoint, SessionStore
from sagewai.core.trust import DeferredInit, TrustLevel
from sagewai.integrations import LiteLLMModel, LiteLLMProxyClient
from sagewai.core.load_balancer import WorkerLoadBalancer
from sagewai.core.monitor import WorkflowMonitor
from sagewai.core.state import ApprovalGate, DurableWorkflow
from sagewai.core.worker import WorkflowWorker, get_worker_credentials

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
from sagewai.mcp.client import McpClient
from sagewai.models.tool import ToolSpec, tool

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
from sagewai.models.agent import AgentConfig
from sagewai.models.inference import InferenceParams
from sagewai.models.message import ChatMessage
from sagewai.models.worker import (
    RoutingConstraints,
    RoutingStrategy as WorkerRoutingStrategy,
    WorkerCredentials,
)

# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------
from sagewai.connectors.base import ConnectorSpec
from sagewai.connectors.registry import ConnectorRegistry

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
from sagewai.notifications.channels.base import NotificationChannel
from sagewai.notifications.channels.email import SMTPChannel
from sagewai.notifications.channels.inapp import InAppChannel
from sagewai.notifications.channels.slack import SlackWebhookChannel
from sagewai.notifications.service import NotificationService

# ---------------------------------------------------------------------------
# Context Engine
# ---------------------------------------------------------------------------
from sagewai.context import (
    ContextEngine,
    ContextScope,
    ContextSource,
)

# ---------------------------------------------------------------------------
# Directive Engine
# ---------------------------------------------------------------------------
from sagewai.directives import (
    DirectiveEngine,
    DirectiveResult,
    InstructionFileLoader,
    ModelProfile,
    detect_profile,
)

# ---------------------------------------------------------------------------
# Intelligence Layer
# ---------------------------------------------------------------------------
from sagewai.intelligence import (
    ConsolidationResult,
    ContentPart,
    ContentType,
    ConversationGraphBuilder,
    Embedder,
    EntityExtractor,
    ExtractedFact,
    ExtractionResult,
    FactExtractor,
    FasterWhisperTranscriber,
    GLiNEREntityExtractor,
    GraphBuildResult,
    HashEmbedder,
    HeuristicRelationExtractor,
    HybridFactExtractor,
    IntelligenceConfig,
    LiteLLMEmbedder,
    LiteLLMTranscriber,
    LLMEntityExtractor,
    LLMFactExtractor,
    MemoryConsolidator,
    LLMRelationExtractor,
    LLMVisionDescriber,
    ProviderRegistry,
    RelationExtractor,
    RelationTriple,
    RuleBasedFactExtractor,
    SemanticSummarizer,
    SentenceTransformerEmbedder,
    StubVisionDescriber,
    Summarizer,
    Transcriber,
    VisionDescriber,
)

# ---------------------------------------------------------------------------
# Fleet (Enterprise)
# ---------------------------------------------------------------------------
from sagewai.fleet import (
    EnrollmentKey,
    FleetAuditBackend,
    FleetAuditEvent,
    FleetAuditEventType,
    FleetDispatcher,
    FleetPayloadEncryption,
    FleetRegistry,
    InMemoryFleetAuditBackend,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    ModelNormalizer,
    PostgresFleetAuditBackend,
    TaskStore,
    WorkerApprovalStatus,
    WorkerCapabilities,
    WorkerRecord,
    WRTTokenManager,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
from sagewai.errors import (
    ContextDocumentNotFoundError,
    ContextIngestionError,
    ContextSearchError,
    SagewaiBudgetExceededError,
    SagewaiConfigError,
    SagewaiContextError,
    SagewaiError,
    SagewaiLLMError,
    SagewaiRateLimitError,
    SagewaiTimeoutError,
    SagewaiToolError,
    SagewaiWorkflowError,
)

__all__ = [
    # Agents
    "agent_as_tool",
    "BaseAgent",
    "ConditionalAgent",
    "GoogleNativeAgent",
    "LoopAgent",
    "ParallelAgent",
    "SequentialAgent",
    "UniversalAgent",
    # Strategies
    "ExecutionStrategy",
    "LATSStrategy",
    "PlanningStrategy",
    "ReActStrategy",
    "RoutingStrategy",
    "SelfCorrectionStrategy",
    "TreeOfThoughtsStrategy",
    # Safety
    "ContentFilter",
    "Guardrail",
    "GuardrailResult",
    "GuardrailViolationError",
    "HallucinationGuard",
    "OutputSchemaGuard",
    "PermissionLevel",
    "PermissionPolicy",
    "PIIGuard",
    "TokenBudgetGuard",
    # Core
    "CompactionPipeline",
    "DeferredInit",
    "HookContext",
    "HookResult",
    "HookRunner",
    "InMemorySessionStore",
    "RuleBasedCompactor",
    "SessionCheckpoint",
    "SessionStore",
    "TrustLevel",
    # Integrations
    "LiteLLMModel",
    "LiteLLMProxyClient",
    # Workflows
    "ApprovalGate",
    "DeadLetterQueue",
    "DurableWorkflow",
    "get_worker_credentials",
    "WorkerCredentials",
    "WorkerLoadBalancer",
    "WorkerRoutingStrategy",
    "WorkflowMonitor",
    "WorkflowWorker",
    # Tools
    "McpClient",
    "tool",
    "ToolSpec",
    # Models
    "AgentConfig",
    "ChatMessage",
    "InferenceParams",
    "RoutingConstraints",
    # Connectors
    "ConnectorRegistry",
    "ConnectorSpec",
    # Notifications
    "InAppChannel",
    "NotificationChannel",
    "NotificationService",
    "SMTPChannel",
    "SlackWebhookChannel",
    # Project Isolation
    "ProjectContext",
    "ProjectQuota",
    "get_current_project",
    "require_project",
    "resolve_project_id",
    # Context Engine
    "ContextEngine",
    "ContextScope",
    "ContextSource",
    # Directive Engine
    "DirectiveEngine",
    "DirectiveResult",
    "InstructionFileLoader",
    "ModelProfile",
    "detect_profile",
    # Fleet (Enterprise)
    "EnrollmentKey",
    "FleetAuditBackend",
    "FleetAuditEvent",
    "FleetAuditEventType",
    "FleetDispatcher",
    "FleetPayloadEncryption",
    "FleetRegistry",
    "InMemoryFleetAuditBackend",
    "InMemoryFleetRegistry",
    "InMemoryTaskStore",
    "ModelNormalizer",
    "PostgresFleetAuditBackend",
    "TaskStore",
    "WorkerApprovalStatus",
    "WorkerCapabilities",
    "WorkerRecord",
    "WRTTokenManager",
    # Intelligence Layer
    "ConsolidationResult",
    "ContentPart",
    "ContentType",
    "ConversationGraphBuilder",
    "Embedder",
    "EntityExtractor",
    "ExtractedFact",
    "ExtractionResult",
    "FactExtractor",
    "FasterWhisperTranscriber",
    "GLiNEREntityExtractor",
    "GraphBuildResult",
    "HashEmbedder",
    "HeuristicRelationExtractor",
    "HybridFactExtractor",
    "IntelligenceConfig",
    "LiteLLMEmbedder",
    "LiteLLMTranscriber",
    "LLMEntityExtractor",
    "LLMFactExtractor",
    "MemoryConsolidator",
    "LLMRelationExtractor",
    "LLMVisionDescriber",
    "ProviderRegistry",
    "RelationExtractor",
    "RelationTriple",
    "RuleBasedFactExtractor",
    "SemanticSummarizer",
    "SentenceTransformerEmbedder",
    "StubVisionDescriber",
    "Summarizer",
    "Transcriber",
    "VisionDescriber",
    # Errors
    "ContextDocumentNotFoundError",
    "ContextIngestionError",
    "ContextSearchError",
    "SagewaiBudgetExceededError",
    "SagewaiConfigError",
    "SagewaiContextError",
    "SagewaiError",
    "SagewaiLLMError",
    "SagewaiRateLimitError",
    "SagewaiTimeoutError",
    "SagewaiToolError",
    "SagewaiWorkflowError",
]
