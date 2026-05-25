/** Admin API client — talks to the sagewai admin router. */

import { createFetchClient } from './api-client';
import { getAccessToken, authFetch } from './auth';
import type {
  AgentAnalytics,
  AgentDetail,
  AgentSummary,
  AgentTemplate,
  AuditEvent,
  AuditEventsResponse,
  AuthTokens,
  AuthUser,
  AvailableModel,
  BudgetLimit,
  BudgetStatus,
  CostAnalytics,
  CreateTokenResponse,
  CursorPage,
  EvalCaseData,
  EvalDatasetDetail,
  EvalDatasetSummary,
  EvalRunDetail,
  EvalRunSummary,
  GraphEntity,
  GraphSearchResponse,
  GraphStats,
  GuardrailConfig,
  Invitation,
  LLMProvider,
  McpCallResponse,
  McpDiscoverResponse,
  McpServer,
  ConnectorCatalogItem,
  ConnectorHealthResult,
  ConnectorTool,
  CreateTriggerRequest,
  CustomConnectorRequest,
  McpServiceConfig,
  McpServiceTestResult,
  ModelAnalytics,
  PromptLogDetail,
  PromptLogSummary,
  ProviderConfig,
  ProviderTestResult,
  ReplayResponse,
  RiskAnalytics,
  RouteTestResponse,
  RoutingRule,
  RunDetail,
  RunSummary,
  SelfHostedProviderTestResult,
  SessionInfo,
  SessionMessagesResponse,
  SetupRequest,
  SetupResponse,
  SetupStatus,
  SystemHealth,
  Project,
  Trigger,
  TokenInfo,
  UsageAnalytics,
  VectorSearchResponse,
  VectorStats,
  Workspace,
  WorkflowRun,
  WorkflowRunDetail,
  WorkflowRunSummary,
  WorkflowSubmitResponse,
  WorkflowTemplate,
  WorkspaceMember,
  LMStudioModelInfo,
  OllamaModelInfo,
  OrgSettings,
  AccountInfo,
  SavePromptRequest,
  UpdatePromptRequest,
  HealthSummary,
  QueueStats,
  WorkerInfo,
  DLQEntry,
  ContextDocument,
  ContextChunk,
  ContextSearchResult,
  ContextStats,
  ContextScopeInfo,
  LifecycleReport,
  MaintenanceReport,
  ContextConflict,
  FleetWorker,
  FleetEnrollmentKey,
  FleetEnrollmentKeyCreate,
  FleetAuditEvent,
  SavedWorkflow,
  BillingPlan,
  BillingSubscription,
  BillingUsage,
  BillingInvoice,
  AutopilotStatus,
  AutopilotGoalResponse,
  AutopilotMissionsResponse,
  BlueprintExplainResponse,
  SandboxRequirementsPayload,
  SandboxRequirementsResponse,
  SandboxResolutionPreview,
  ProfileMetadata,
  Profile,
  ProfileWritePayload,
  SealedAuditEvent,
  SealedStatus,
  SealedSystemConfig,
  SealedWorkflowConfig,
  EffectiveProfile,
  Revocation,
  PoolStatsSnapshot,
  ArtifactDestination,
  ReplayPreview,
  ReplayInfo,
  ReplayCommitResult,
  DirectivesConfig,
  DirectivePolicy,
  DirectiveEvaluation,
  PendingApproval,
  InferenceProviderCatalog,
  InferenceProviderMetadata,
  InferenceProviderWritePayload,
  InferenceProviderTestResult,
  InferenceProviderKey,
  AutopilotMissionDetail,
  AutopilotMissionExplain,
  AutopilotMissionTrace,
  MissionRunEvent,
  ToolRegistryEntry,
  ToolConnectionMetadata,
  ToolTestResult,
} from './types';
import { createConnectionsApi } from './connections-api';

const BASE_URL = process.env.NEXT_PUBLIC_ADMIN_API_URL ?? 'http://localhost:8000/admin';
const ANALYTICS_URL = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';
import { getCurrentProjectId } from './project-state';

const clientOpts = { getToken: getAccessToken, getProjectId: getCurrentProjectId };
const client = createFetchClient(BASE_URL, clientOpts);
const analyticsClient = createFetchClient(ANALYTICS_URL, clientOpts);


export const adminApi = {
  /* ─── Unified connections (PR5) — talks to /api/v1/admin/connections/... ─── */
  connections: createConnectionsApi(analyticsClient),

  /* ─── Core admin endpoints ─── */
  listAgents: () => client.get<AgentSummary[]>('/agents'),

  getAgent: (name: string) => client.get<AgentDetail>(`/agents/${encodeURIComponent(name)}`),

  listRuns: (params?: {
    agent_name?: string;
    status?: string;
    run_type?: string;
    include_workflow_steps?: boolean;
    cursor?: string;
    limit?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.agent_name) sp.set('agent_name', params.agent_name);
    if (params?.status) sp.set('status', params.status);
    if (params?.run_type) sp.set('run_type', params.run_type);
    if (params?.include_workflow_steps) sp.set('include_workflow_steps', 'true');
    if (params?.cursor) sp.set('cursor', params.cursor);
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return client.get<CursorPage<RunSummary>>(`/runs${qs ? `?${qs}` : ''}`);
  },

  getRun: (runId: string) => client.get<RunDetail>(`/runs/${encodeURIComponent(runId)}`),

  listSessions: (params?: { cursor?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.cursor) sp.set('cursor', params.cursor);
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return client.get<CursorPage<SessionInfo>>(`/sessions${qs ? `?${qs}` : ''}`);
  },

  getSession: (sessionId: string) =>
    client.get<SessionInfo>(`/sessions/${encodeURIComponent(sessionId)}`),

  /* ─── Analytics endpoints ─── */
  getCosts: (agentName?: string) => {
    const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : '';
    return analyticsClient.get<CostAnalytics>(`/api/v1/analytics/costs${qs}`);
  },

  getUsage: (agentName?: string) => {
    const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : '';
    return analyticsClient.get<UsageAnalytics>(`/api/v1/analytics/usage${qs}`);
  },

  getRisks: (agentName?: string) => {
    const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : '';
    return analyticsClient.get<RiskAnalytics>(`/api/v1/analytics/risks${qs}`);
  },

  getModelAnalytics: () =>
    analyticsClient.get<ModelAnalytics[]>('/api/v1/analytics/models'),

  getAgentAnalytics: () =>
    analyticsClient.get<AgentAnalytics[]>('/api/v1/analytics/agents'),

  /* ─── Budget endpoints ─── */
  listBudgetLimits: () =>
    analyticsClient.get<BudgetLimit[]>('/api/v1/budget/limits'),

  createBudgetLimit: (limit: BudgetLimit) =>
    analyticsClient.post<BudgetLimit>('/api/v1/budget/limits', limit),

  updateBudgetLimit: (agentName: string, limit: BudgetLimit) =>
    analyticsClient.put<BudgetLimit>(
      `/api/v1/budget/limits/${encodeURIComponent(agentName)}`,
      limit,
    ),

  deleteBudgetLimit: (agentName: string) =>
    analyticsClient.delete<{ deleted: string }>(
      `/api/v1/budget/limits/${encodeURIComponent(agentName)}`,
    ),

  getBudgetStatus: (agentName: string) =>
    analyticsClient.get<BudgetStatus>(
      `/api/v1/budget/status/${encodeURIComponent(agentName)}`,
    ),

  /* ─── Run control endpoints ─── */
  pauseRun: (id: string) =>
    client.post<{ status: string }>(`/runs/${encodeURIComponent(id)}/pause`, {}),

  resumeRun: (id: string) =>
    client.post<{ status: string }>(`/runs/${encodeURIComponent(id)}/resume`, {}),

  cancelRun: (id: string) =>
    client.post<{ status: string }>(`/runs/${encodeURIComponent(id)}/cancel`, {}),

  /* ─── Agent config endpoints ─── */
  updateAgentConfig: (name: string, config: Record<string, unknown>) =>
    client.raw<AgentDetail>(`/agents/${encodeURIComponent(name)}/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),

  deleteAgent: (name: string) =>
    authFetch(
      `${ANALYTICS_URL}/playground/agents/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ).then(async (r) => {
      if (!r.ok) throw new Error(`Delete failed: ${r.status}`);
      return r.json() as Promise<{ deleted: string }>;
    }),

  renameAgent: (name: string, newName: string) =>
    authFetch(
      `${ANALYTICS_URL}/playground/agents/${encodeURIComponent(name)}/rename`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName }),
      },
    ).then(async (r) => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? `Rename failed: ${r.status}`);
      }
      return r.json() as Promise<{ old_name: string; new_name: string }>;
    }),

  /* ─── Guardrails endpoints ─── */
  listGuardrailConfigs: (agentName?: string) => {
    const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : '';
    return analyticsClient.get<GuardrailConfig[]>(`/api/v1/guardrails/configs${qs}`);
  },

  upsertGuardrailConfig: (
    agentName: string,
    guardrailType: string,
    enabled: boolean,
    config?: Record<string, unknown> | null,
  ) =>
    analyticsClient.put<GuardrailConfig>(
      `/api/v1/guardrails/configs/${encodeURIComponent(agentName)}`,
      { guardrail_type: guardrailType, enabled, config: config ?? null },
    ),

  deleteGuardrailConfig: (agentName: string, guardrailType: string) =>
    analyticsClient.delete<{ deleted: string }>(
      `/api/v1/guardrails/configs/${encodeURIComponent(agentName)}/${encodeURIComponent(guardrailType)}`,
    ),

  /* ─── Audit endpoints ─── */
  listAuditEvents: (params?: {
    agent_name?: string;
    event_type?: string;
    cursor?: string;
    limit?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.agent_name) sp.set('agent_name', params.agent_name);
    if (params?.event_type) sp.set('event_type', params.event_type);
    if (params?.cursor) sp.set('cursor', params.cursor);
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return analyticsClient.get<CursorPage<AuditEvent>>(`/api/v1/audit/events${qs ? `?${qs}` : ''}`);
  },

  /* ─── Memory: Vector endpoints ─── */
  getVectorStats: () =>
    analyticsClient.get<VectorStats>('/api/v1/memory/vector/stats'),

  vectorSearch: (query: string, topK = 5) =>
    analyticsClient.post<VectorSearchResponse>('/api/v1/memory/vector/search', {
      query,
      top_k: topK,
    }),

  vectorIngest: (content: string, metadata?: Record<string, unknown>) =>
    analyticsClient.post<{ status: string }>('/api/v1/memory/vector/ingest', {
      content,
      metadata: metadata ?? null,
    }),

  /* ─── Memory: Graph endpoints ─── */
  getGraphStats: () =>
    analyticsClient.get<GraphStats>('/api/v1/memory/graph/stats'),

  graphQuery: (query: string, topK = 5) =>
    analyticsClient.post<GraphSearchResponse>('/api/v1/memory/graph/query', {
      query,
      top_k: topK,
    }),

  graphAddEntity: (name: string, metadata?: Record<string, unknown>) =>
    analyticsClient.post<{ status: string; entity: string }>(
      '/api/v1/memory/graph/entity',
      { name, metadata: metadata ?? null },
    ),

  graphAddRelation: (source: string, relation: string, target: string) =>
    analyticsClient.post<{ status: string; relation: string }>(
      '/api/v1/memory/graph/relation',
      { source, relation, target },
    ),

  graphGetEntity: (name: string) =>
    analyticsClient.get<GraphEntity>(
      `/api/v1/memory/graph/entity/${encodeURIComponent(name)}`,
    ),

  graphListEntities: (params?: { search?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.search) sp.set('search', params.search);
    if (params?.limit) sp.set('limit', String(params.limit));
    if (params?.offset) sp.set('offset', String(params.offset));
    const qs = sp.toString();
    return analyticsClient.get<{ entities: Array<{ name: string; metadata: Record<string, unknown> }>; count: number }>(
      `/api/v1/memory/graph/entities${qs ? `?${qs}` : ''}`,
    );
  },

  graphGetRelations: (name: string) =>
    analyticsClient.get<{
      entity: string;
      relations: Array<{ source: string; relation: string; target: string }>;
      count: number;
    }>(`/api/v1/memory/graph/entity/${encodeURIComponent(name)}/relations`),

  graphGetNeighbors: (name: string, depth = 1) =>
    analyticsClient.get<{
      entity: string;
      depth: number;
      neighbors: Array<{ entity: string; relation: string }>;
      count: number;
    }>(`/api/v1/memory/graph/entity/${encodeURIComponent(name)}/neighbors?depth=${depth}`),

  /* ─── Eval endpoints ─── */
  listEvalDatasets: () =>
    analyticsClient.get<EvalDatasetSummary[]>('/api/v1/eval/datasets'),

  createEvalDataset: (name: string, cases: EvalCaseData[], description?: string) =>
    analyticsClient.post<EvalDatasetSummary>('/api/v1/eval/datasets', {
      name,
      description: description ?? null,
      cases,
    }),

  getEvalDataset: (id: number) =>
    analyticsClient.get<EvalDatasetDetail>(`/api/v1/eval/datasets/${id}`),

  deleteEvalDataset: (id: number) =>
    analyticsClient.delete<{ deleted: string }>(`/api/v1/eval/datasets/${id}`),

  runEval: (datasetId: number, agentName: string, judgeModel?: string) =>
    analyticsClient.post<EvalRunDetail>('/api/v1/eval/run', {
      dataset_id: datasetId,
      agent_name: agentName,
      judge_model: judgeModel ?? 'gpt-4o-mini',
    }),

  listEvalRuns: (datasetId?: number) => {
    const qs = datasetId ? `?dataset_id=${datasetId}` : '';
    return analyticsClient.get<EvalRunSummary[]>(`/api/v1/eval/runs${qs}`);
  },

  getEvalRun: (id: number) =>
    analyticsClient.get<EvalRunDetail>(`/api/v1/eval/runs/${id}`),

  /* ─── MCP endpoints ─── */
  listMcpServers: () =>
    analyticsClient.get<McpServer[]>('/api/v1/mcp/servers'),

  discoverMcpTools: (serverCmd: string) =>
    analyticsClient.post<McpDiscoverResponse>('/api/v1/mcp/discover', {
      server_cmd: serverCmd,
    }),

  callMcpTool: (serverCmd: string, toolName: string, args?: Record<string, unknown>) =>
    analyticsClient.post<McpCallResponse>('/api/v1/mcp/call', {
      server_cmd: serverCmd,
      tool_name: toolName,
      arguments: args ?? {},
    }),

  /* ─── Model Router endpoints ─── */
  listRoutingRules: () =>
    analyticsClient.get<RoutingRule[]>('/api/v1/model-router/rules'),

  testRoute: (query: string, context?: Record<string, unknown>, defaultModel?: string) =>
    analyticsClient.post<RouteTestResponse>('/api/v1/model-router/test', {
      query,
      context: context ?? {},
      default_model: defaultModel ?? 'gpt-4o',
    }),

  listAvailableModels: () =>
    analyticsClient.get<AvailableModel[]>('/api/v1/model-router/models'),

  /* ─── Prompt Store endpoints ─── */
  listPromptLogs: (params?: {
    agent_name?: string;
    model?: string;
    run_id?: string;
    cursor?: string;
    limit?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.agent_name) sp.set('agent_name', params.agent_name);
    if (params?.model) sp.set('model', params.model);
    if (params?.run_id) sp.set('run_id', params.run_id);
    if (params?.cursor) sp.set('cursor', params.cursor);
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return analyticsClient.get<CursorPage<PromptLogSummary>>(
      `/api/v1/prompts/logs${qs ? `?${qs}` : ''}`,
    );
  },

  getPromptLog: (logId: string) =>
    analyticsClient.get<PromptLogDetail>(
      `/api/v1/prompts/logs/${encodeURIComponent(logId)}`,
    ),

  replayPrompt: (logId: string, model: string) =>
    analyticsClient.post<ReplayResponse>('/api/v1/prompts/replay', {
      log_id: logId,
      model,
    }),

  /* ─── Token endpoints ─── */
  listTokens: (agentName?: string) => {
    const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : '';
    return analyticsClient.get<TokenInfo[]>(`/api/v1/tokens/${qs}`);
  },

  createToken: (agentName: string, scopes?: string[], expiresIn?: number) =>
    analyticsClient.post<CreateTokenResponse>('/api/v1/tokens/', {
      agent_name: agentName,
      scopes: scopes ?? ['chat'],
      expires_in_seconds: expiresIn ?? 86400,
    }),

  revokeToken: (tokenId: string) =>
    analyticsClient.post<{ token_id: string; status: string }>(
      `/api/v1/tokens/${encodeURIComponent(tokenId)}/revoke`,
      {},
    ),

  deleteToken: (tokenId: string) =>
    analyticsClient.delete<{ token_id: string; deleted: boolean }>(
      `/api/v1/tokens/${encodeURIComponent(tokenId)}`,
    ),

  /* ─── Session detail endpoints ─── */
  getSessionMessages: (sessionId: string) =>
    analyticsClient.get<SessionMessagesResponse>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
    ),

  /* ─── Workflow endpoints (durable execution) ─── */
  submitWorkflow: (yaml: string, message: string, idempotencyKey?: string) =>
    analyticsClient.post<WorkflowSubmitResponse>('/workflows/run', {
      yaml,
      message,
      idempotency_key: idempotencyKey ?? null,
    }),

  getWorkflowRun: (runId: string) =>
    analyticsClient.get<WorkflowRun>(`/workflows/runs/${encodeURIComponent(runId)}`),

  cancelWorkflowRun: (runId: string) =>
    analyticsClient.post<{ cancelled: boolean; run_id: string }>(
      `/workflows/runs/${encodeURIComponent(runId)}/cancel`,
      {},
    ),

  listWorkflowRuns: (params?: { limit?: number; offset?: number; status?: string; search?: string }) => {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set('limit', String(params.limit));
    if (params?.offset) sp.set('offset', String(params.offset));
    if (params?.status) sp.set('status', params.status);
    if (params?.search) sp.set('search', params.search);
    const qs = sp.toString();
    return analyticsClient.get<WorkflowRun[]>(`/workflows/history${qs ? `?${qs}` : ''}`);
  },

  /** @deprecated Use listWorkflowRuns instead */
  listWorkflowHistory: (limit = 50) =>
    analyticsClient.get<WorkflowRunSummary[]>(`/workflows/history?limit=${limit}`),

  /** @deprecated Use getWorkflowRun instead */
  getWorkflowHistoryDetail: (runId: string) =>
    analyticsClient.get<WorkflowRunDetail>(
      `/workflows/history/${encodeURIComponent(runId)}`,
    ),

  /* ─── Workflow templates endpoint ─── */
  listWorkflowTemplates: () =>
    analyticsClient.get<WorkflowTemplate[]>('/workflows/templates'),

  /* ─── Workflow dashboard endpoints ─── */
  getWorkflowStats: () =>
    analyticsClient.get<QueueStats>('/workflows/stats'),

  listWorkers: () =>
    analyticsClient.get<WorkerInfo[]>('/workflows/workers'),

  listDLQ: (params?: { limit?: number; offset?: number; workflow_name?: string }) => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    if (params?.workflow_name) qs.set('workflow_name', params.workflow_name);
    return analyticsClient.get<DLQEntry[]>(`/workflows/dlq${qs.toString() ? `?${qs}` : ''}`);
  },

  retryDLQ: (runId: string, priority = 0) =>
    analyticsClient.post<{ new_run_id: string }>(`/workflows/dlq/${encodeURIComponent(runId)}/retry`, { priority }),

  discardDLQ: (runId: string) =>
    analyticsClient.delete(`/workflows/dlq/${encodeURIComponent(runId)}`),

  listApprovals: () =>
    analyticsClient.get<WorkflowRun[]>('/workflows/approvals'),

  approveWorkflow: (runId: string, comment = '') =>
    analyticsClient.post(`/workflows/runs/${encodeURIComponent(runId)}/approve`, { comment }),

  rejectWorkflow: (runId: string, reason = '') =>
    analyticsClient.post(`/workflows/runs/${encodeURIComponent(runId)}/reject`, { reason }),

  dispatchWorkflow: (workflowName: string, inputData: Record<string, unknown> = {}, priority = 0) =>
    analyticsClient.post<{ run_id: string; is_new: boolean }>('/workflows/dispatch', {
      workflow_name: workflowName,
      input_data: inputData,
      priority,
    }),

  /* ─── Workflow registry endpoints ─── */
  listSavedWorkflows: (params?: { search?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.search) sp.set('search', params.search);
    if (params?.limit) sp.set('limit', String(params.limit));
    if (params?.offset) sp.set('offset', String(params.offset));
    const qs = sp.toString();
    return analyticsClient.get<{ items: SavedWorkflow[]; total: number }>(
      `/api/v1/workflow-registry${qs ? `?${qs}` : ''}`,
    );
  },

  saveWorkflow: (data: { name: string; yaml_content: string; description?: string }) =>
    analyticsClient.post<SavedWorkflow>('/api/v1/workflow-registry', data),

  getSavedWorkflow: (id: string) =>
    analyticsClient.get<SavedWorkflow>(`/api/v1/workflow-registry/${encodeURIComponent(id)}`),

  getSavedWorkflowByName: (name: string) =>
    analyticsClient.get<SavedWorkflow>(`/api/v1/workflow-registry/by-name/${encodeURIComponent(name)}`),

  updateSavedWorkflow: (id: string, data: { yaml_content?: string; description?: string }) =>
    analyticsClient.put<SavedWorkflow>(`/api/v1/workflow-registry/${encodeURIComponent(id)}`, data),

  deleteSavedWorkflow: (id: string) =>
    analyticsClient.delete(`/api/v1/workflow-registry/${encodeURIComponent(id)}`),

  listWorkflowVersions: (id: string) =>
    analyticsClient.get<{ version: number; yaml_content: string; created_at: number }[]>(
      `/api/v1/workflow-registry/${encodeURIComponent(id)}/versions`,
    ),

  /* ─── System health endpoints ─── */
  getSystemHealth: () => analyticsClient.get<SystemHealth>('/api/v1/health/detailed'),

  getHealthSummary: () =>
    analyticsClient.get<HealthSummary>('/api/v1/health/summary'),

  /* ─── Setup endpoints ─── */
  getSetupStatus: () =>
    analyticsClient.get<SetupStatus>('/api/v1/setup/status'),

  runSetup: (data: SetupRequest) =>
    analyticsClient.post<SetupResponse>('/api/v1/setup', data),

  /* ─── Self-hosted provider config endpoints ─── */
  listProviderConfigs: () =>
    analyticsClient.get<ProviderConfig[]>('/api/v1/providers'),

  upsertProviderConfig: (data: { provider_name: string; provider_type: string; display_name: string; config: Record<string, string> }) =>
    analyticsClient.post<{ id: string }>('/api/v1/providers', data),

  testProviderConfig: (id: string) =>
    analyticsClient.post<SelfHostedProviderTestResult>(`/api/v1/providers/${encodeURIComponent(id)}/test`, {}),

  deleteProviderConfig: (id: string) =>
    analyticsClient.delete<void>(`/api/v1/providers/${encodeURIComponent(id)}`),

  listOllamaModels: () =>
    analyticsClient.get<{ connected: boolean; models: OllamaModelInfo[]; error?: string }>('/api/v1/providers/ollama/models'),

  listLMStudioModels: () =>
    analyticsClient.get<{ connected: boolean; endpoint?: string; models: LMStudioModelInfo[]; error?: string }>('/api/v1/providers/lmstudio/models'),

  /* ─── MCP service config endpoints (legacy) ─── */
  listMcpServiceConfigs: () =>
    analyticsClient.get<McpServiceConfig[]>('/api/v1/mcp-services'),

  upsertMcpServiceConfig: (data: { service_name: string; display_name?: string; config: Record<string, string> }) =>
    analyticsClient.post<{ id: string; status: string }>('/api/v1/mcp-services', data),

  testMcpServiceConfig: (id: string) =>
    analyticsClient.post<McpServiceTestResult>(`/api/v1/mcp-services/${encodeURIComponent(id)}/test`, {}),

  deleteMcpServiceConfig: (id: string) =>
    analyticsClient.delete<void>(`/api/v1/mcp-services/${encodeURIComponent(id)}`),

  /* ─── Connector catalog endpoints ─── */
  listConnectors: () =>
    analyticsClient.get<ConnectorCatalogItem[]>('/api/v1/connectors'),

  saveConnectorCredentials: (name: string, credentials: Record<string, string>) =>
    analyticsClient.post<{ name: string; status: string }>(
      `/api/v1/connectors/${encodeURIComponent(name)}`,
      { credentials },
    ),

  testConnector: (name: string) =>
    analyticsClient.post<ConnectorHealthResult>(
      `/api/v1/connectors/${encodeURIComponent(name)}/test`,
      {},
    ),

  deleteConnector: (name: string) =>
    analyticsClient.delete<{ name: string; deleted: boolean }>(
      `/api/v1/connectors/${encodeURIComponent(name)}`,
    ),

  discoverConnectorTools: (name: string) =>
    analyticsClient.post<{ connector: string; tools: ConnectorTool[]; count: number }>(
      `/api/v1/connectors/${encodeURIComponent(name)}/tools`,
      {},
    ),

  /* ─── Custom connector endpoints ─── */
  registerCustomConnector: (data: CustomConnectorRequest) =>
    analyticsClient.post<{ status: string; connector: string }>(
      '/api/v1/connectors/custom',
      data,
    ),

  updateCustomConnector: (name: string, data: CustomConnectorRequest) =>
    analyticsClient.raw<{ status: string; connector: string }>(
      `/api/v1/connectors/custom/${encodeURIComponent(name)}`,
      { method: 'PUT', body: JSON.stringify(data), headers: { 'Content-Type': 'application/json' } },
    ),

  deleteCustomConnector: (name: string) =>
    analyticsClient.delete<{ status: string; connector: string }>(
      `/api/v1/connectors/custom/${encodeURIComponent(name)}`,
    ),

  /* ─── Trigger endpoints ─── */
  listTriggers: () =>
    analyticsClient.get<Trigger[]>('/api/v1/triggers'),

  createTrigger: (data: CreateTriggerRequest) =>
    analyticsClient.post<{ status: string; id: string }>('/api/v1/triggers', data),

  deleteTrigger: (id: string) =>
    analyticsClient.delete<{ status: string; id: string }>(
      `/api/v1/triggers/${encodeURIComponent(id)}`,
    ),

  enableTrigger: (id: string) =>
    analyticsClient.raw<{ status: string; id: string }>(
      `/api/v1/triggers/${encodeURIComponent(id)}/enable`,
      { method: 'PATCH' },
    ),

  disableTrigger: (id: string) =>
    analyticsClient.raw<{ status: string; id: string }>(
      `/api/v1/triggers/${encodeURIComponent(id)}/disable`,
      { method: 'PATCH' },
    ),

  /* ─── Project endpoints ─── */
  listProjects: () =>
    analyticsClient.get<Project[]>('/api/v1/projects'),

  createProject: (data: { name: string; slug?: string; environment?: string; allowed_origins?: string }) =>
    analyticsClient.post<Project>('/api/v1/projects', data),

  getProject: (slug: string) =>
    analyticsClient.get<Project>(`/api/v1/projects/${encodeURIComponent(slug)}`),

  updateProject: (slug: string, data: { name?: string; environment?: string; allowed_origins?: string; status?: string; default_model?: string }) =>
    analyticsClient.raw<Project>(`/api/v1/projects/${encodeURIComponent(slug)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  deleteProject: (slug: string) =>
    analyticsClient.delete<{ status: string }>(`/api/v1/projects/${encodeURIComponent(slug)}`),

  /* ─── Agent Template endpoints ─── */
  listAgentTemplates: () =>
    analyticsClient.get<AgentTemplate[]>('/api/v1/agents/templates'),

  getAgentTemplate: (id: string) =>
    analyticsClient.get<AgentTemplate>(`/api/v1/agents/templates/${encodeURIComponent(id)}`),

  /* ─── Cloud Auth endpoints (cloud mode only) ─── */
  register: (email: string, password: string, displayName?: string) =>
    analyticsClient.post<AuthTokens>('/api/v1/auth/register', {
      email,
      password,
      display_name: displayName ?? '',
    }),

  login: (email: string, password: string) =>
    analyticsClient.post<AuthTokens>('/api/v1/auth/login', { email, password }),

  refreshToken: () =>
    analyticsClient.raw<AuthTokens>('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include' as RequestCredentials,
    }),

  getMe: () => analyticsClient.get<AuthUser>('/api/v1/auth/me'),

  updateMe: (displayName?: string, avatarUrl?: string) =>
    analyticsClient.raw<AuthUser>('/api/v1/auth/me', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: displayName, avatar_url: avatarUrl }),
    }),

  /* ─── Workspace endpoints (cloud mode only) ─── */
  listWorkspaces: () =>
    analyticsClient.get<Workspace[]>('/api/v1/workspaces'),

  createWorkspace: (name: string, settings?: Record<string, unknown>) =>
    analyticsClient.post<Workspace>('/api/v1/workspaces', { name, settings }),

  getWorkspace: (id: string) =>
    analyticsClient.get<Workspace>(`/api/v1/workspaces/${encodeURIComponent(id)}`),

  updateWorkspace: (id: string, name?: string, settings?: Record<string, unknown>) =>
    analyticsClient.raw<Workspace>(
      `/api/v1/workspaces/${encodeURIComponent(id)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, settings }),
      },
    ),

  deleteWorkspace: (id: string) =>
    analyticsClient.delete<{ deleted: string }>(
      `/api/v1/workspaces/${encodeURIComponent(id)}`,
    ),

  /* ─── Member endpoints (cloud mode only) ─── */
  listMembers: (workspaceId: string) =>
    analyticsClient.get<WorkspaceMember[]>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
    ),

  inviteMember: (workspaceId: string, email: string, role?: string) =>
    analyticsClient.post<Invitation>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members/invite`,
      { email, role: role ?? 'member' },
    ),

  updateMemberRole: (workspaceId: string, userId: string, role: string) =>
    analyticsClient.raw<WorkspaceMember>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      },
    ),

  removeMember: (workspaceId: string, userId: string) =>
    analyticsClient.delete<{ removed: string }>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
    ),

  /* ─── Provider endpoints (cloud mode only) ─── */
  listProviders: (workspaceId: string) =>
    analyticsClient.get<LLMProvider[]>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/providers`,
    ),

  addProvider: (
    workspaceId: string,
    providerName: string,
    apiKey: string,
    displayName?: string,
    isDefault?: boolean,
  ) =>
    analyticsClient.post<LLMProvider>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/providers`,
      {
        provider_name: providerName,
        api_key: apiKey,
        display_name: displayName ?? '',
        is_default: isDefault ?? false,
      },
    ),

  deleteProvider: (workspaceId: string, providerId: number) =>
    analyticsClient.delete<{ deleted: number }>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/providers/${providerId}`,
    ),

  testProvider: (workspaceId: string, providerId: number) =>
    analyticsClient.post<ProviderTestResult>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/providers/${providerId}/test`,
      {},
    ),

  /* ─── Organization settings ─── */
  getOrganization: () => analyticsClient.get<OrgSettings>('/api/v1/organization'),

  updateOrganization: (body: Record<string, unknown>) =>
    analyticsClient.patch<OrgSettings>('/api/v1/organization', body),

  testLiteLLM: (body: { proxy_url: string; api_key?: string }) =>
    analyticsClient.post<import('./types').TestLiteLLMResponse>(
      '/api/v1/organization/test-litellm', body,
    ),

  /* ─── Account settings ─── */
  getAccount: () => analyticsClient.get<AccountInfo>('/api/v1/account'),

  updateProfile: (body: { display_name: string }) =>
    analyticsClient.patch<AccountInfo>('/api/v1/account/profile', body),

  changePassword: (body: { current_password: string; new_password: string }) =>
    analyticsClient.post<{ ok: boolean; message: string }>('/api/v1/account/password', body),

  /* ─── Prompt save/update/delete endpoints (unified store) ─── */
  savePrompt: (data: SavePromptRequest) =>
    analyticsClient.post<{ log_id: string }>('/api/v1/prompts/logs', data),

  updatePromptLog: (logId: string, data: UpdatePromptRequest) =>
    analyticsClient.raw<PromptLogDetail>(`/api/v1/prompts/logs/${encodeURIComponent(logId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  deletePromptLog: (logId: string) =>
    analyticsClient.delete<{ log_id: string; deleted: string }>(
      `/api/v1/prompts/logs/${encodeURIComponent(logId)}`,
    ),

  listExamples: (agentName: string, limit = 50) =>
    analyticsClient.get<PromptLogDetail[]>(
      `/api/v1/prompts/examples?agent_name=${encodeURIComponent(agentName)}&limit=${limit}`,
    ),

  /* ─── Premium analytics endpoints ─── */
  getAgentNetwork: (from?: string, to?: string) => {
    const sp = new URLSearchParams();
    if (from) sp.set('from', from);
    if (to) sp.set('to', to);
    const qs = sp.toString();
    return analyticsClient.get<{
      nodes: Array<{ id: string; tokens: number; runs: number; error_rate: number }>;
      edges: Array<{ source: string; target: string; weight: number }>;
    }>(`/api/v1/analytics/agent-network${qs ? `?${qs}` : ''}`);
  },

  getWorkflowHeatmap: (days = 90) =>
    analyticsClient.get<{
      data: Array<{
        workflow_name: string;
        date: string;
        total_runs: number;
        passed: number;
        failed: number;
        avg_duration_ms: number;
        p95_duration_ms: number;
      }>;
    }>(`/api/v1/analytics/workflow-heatmap?days=${days}`),

  /* ─── Notification endpoints ─── */
  listNotificationChannels: (projectId?: string) => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return analyticsClient.get<Record<string, unknown>[]>(`/api/v1/notifications/channels${qs}`);
  },

  saveNotificationChannel: (config: Record<string, unknown>) =>
    analyticsClient.post<{ id: string; status: string; channel_type: string }>(
      '/api/v1/notifications/channels',
      config,
    ),

  deleteNotificationChannel: (id: string) =>
    analyticsClient.delete<{ deleted: string }>(
      `/api/v1/notifications/channels/${encodeURIComponent(id)}`,
    ),

  listNotificationTriggers: (projectId?: string) => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return analyticsClient.get<Record<string, unknown>[]>(`/api/v1/notifications/triggers${qs}`);
  },

  saveNotificationTrigger: (config: { trigger: string; channel_type: string; enabled?: boolean; project_id?: string }) =>
    analyticsClient.post<{ id: string; status: string }>(
      '/api/v1/notifications/triggers',
      config,
    ),

  deleteNotificationTrigger: (id: string) =>
    analyticsClient.delete<{ deleted: string }>(
      `/api/v1/notifications/triggers/${encodeURIComponent(id)}`,
    ),

  listNotificationHistory: (params?: { limit?: number; offset?: number; trigger?: string; project_id?: string }) => {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set('limit', String(params.limit));
    if (params?.offset) sp.set('offset', String(params.offset));
    if (params?.trigger) sp.set('trigger', params.trigger);
    if (params?.project_id) sp.set('project_id', params.project_id);
    const qs = sp.toString();
    return analyticsClient.get<Record<string, unknown>[]>(`/api/v1/notifications/history${qs ? `?${qs}` : ''}`);
  },

  testNotification: (config: { channel_type: 'email' | 'slack' | 'in_app'; project_id?: string }) =>
    analyticsClient.post<{ sent: boolean; error?: string }>(
      '/api/v1/notifications/test',
      config,
    ),

  /* ─── Context Engine endpoints ─── */

  getContextStats: () =>
    analyticsClient.get<ContextStats>('/api/v1/context/stats'),

  getContextScopes: () =>
    analyticsClient.get<{ scopes: ContextScopeInfo[] }>('/api/v1/context/scopes'),

  listContextDocuments: (params?: {
    scope?: string;
    scope_id?: string;
    source?: string;
    status?: string;
    tags?: string;
    sort_by?: string;
    sort_order?: string;
    limit?: number;
    offset?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.scope) sp.set('scope', params.scope);
    if (params?.scope_id) sp.set('scope_id', params.scope_id);
    if (params?.source) sp.set('source', params.source);
    if (params?.status) sp.set('status', params.status);
    if (params?.tags) sp.set('tags', params.tags);
    if (params?.sort_by) sp.set('sort_by', params.sort_by);
    if (params?.sort_order) sp.set('sort_order', params.sort_order);
    if (params?.limit != null) sp.set('limit', String(params.limit));
    if (params?.offset != null) sp.set('offset', String(params.offset));
    const qs = sp.toString();
    return analyticsClient.get<{ documents: ContextDocument[]; count: number; total: number }>(
      `/api/v1/context/documents${qs ? `?${qs}` : ''}`,
    );
  },

  getContextDocument: (docId: string) =>
    analyticsClient.get<{ document: ContextDocument; chunks: ContextChunk[] }>(
      `/api/v1/context/documents/${encodeURIComponent(docId)}`,
    ),

  uploadContextDocument: (file: File, scope: string, scopeId: string, enableGraph = false, tags: string[] = []) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('scope', scope);
    formData.append('scope_id', scopeId);
    formData.append('enable_graph', String(enableGraph));
    if (tags.length > 0) formData.append('tags', tags.join(','));
    return analyticsClient.raw<{ status: string; filename: string; message: string }>(
      '/api/v1/context/documents',
      { method: 'POST', body: formData },
    );
  },

  ingestContextText: (body: {
    text: string;
    title: string;
    scope?: string;
    scope_id?: string;
    source?: string;
    metadata?: Record<string, unknown>;
    tags?: string[];
  }) =>
    analyticsClient.post<{ status: string; title: string; message: string }>(
      '/api/v1/context/documents/text',
      {
        text: body.text,
        title: body.title,
        scope: body.scope ?? 'project',
        scope_id: body.scope_id ?? '',
        source: body.source ?? 'manual',
        metadata: body.metadata ?? null,
        tags: body.tags ?? [],
      },
    ),

  updateContextDocument: (docId: string, body: {
    status?: string;
    confidence?: number;
    metadata?: Record<string, unknown>;
  }) =>
    analyticsClient.raw<{ document: ContextDocument }>(
      `/api/v1/context/documents/${encodeURIComponent(docId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),

  deleteContextDocument: (docId: string) =>
    analyticsClient.delete<{ status: string; document_id: string }>(
      `/api/v1/context/documents/${encodeURIComponent(docId)}`,
    ),

  batchDeleteContextDocuments: (docIds: string[]) =>
    analyticsClient.post<{ status: string; deleted: number }>(
      '/api/v1/context/documents/batch-delete',
      { document_ids: docIds },
    ),

  ingestContextDirectory: (body: {
    path: string;
    scope?: string;
    scope_id?: string;
    patterns?: string[];
    ignore?: string[];
    enable_graph?: boolean;
  }) =>
    analyticsClient.post<{ status: string; path: string; message: string }>(
      '/api/v1/context/documents/directory',
      {
        path: body.path,
        scope: body.scope ?? 'project',
        scope_id: body.scope_id ?? '',
        patterns: body.patterns ?? null,
        ignore: body.ignore ?? null,
        enable_graph: body.enable_graph ?? false,
      },
    ),

  reprocessContextDocument: (docId: string) =>
    analyticsClient.post<{ document: ContextDocument }>(
      `/api/v1/context/documents/${encodeURIComponent(docId)}/reprocess`,
      {},
    ),

  listContextChunks: (docId: string) =>
    analyticsClient.get<{ chunks: ContextChunk[]; count: number }>(
      `/api/v1/context/documents/${encodeURIComponent(docId)}/chunks`,
    ),

  updateContextChunk: (chunkId: string, body: {
    content?: string;
    importance?: number;
  }) =>
    analyticsClient.raw<{ chunk: ContextChunk }>(
      `/api/v1/context/chunks/${encodeURIComponent(chunkId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),

  deleteContextChunk: (chunkId: string) =>
    analyticsClient.delete<{ status: string; chunk_id: string }>(
      `/api/v1/context/chunks/${encodeURIComponent(chunkId)}`,
    ),

  contextSearch: (body: {
    query: string;
    top_k?: number;
    scopes?: string[];
    sources?: string[];
  }) =>
    analyticsClient.post<{
      query: string;
      results: ContextSearchResult[];
      count: number;
    }>('/api/v1/context/search', {
      query: body.query,
      top_k: body.top_k ?? 5,
      scopes: body.scopes ?? null,
      sources: body.sources ?? null,
    }),

  listContextMemories: (scope: string, scopeId: string) =>
    analyticsClient.get<{
      scope: string;
      scope_id: string;
      memories: ContextDocument[];
      count: number;
    }>(`/api/v1/context/memory/${encodeURIComponent(scope)}/${encodeURIComponent(scopeId)}`),

  /** @deprecated Use listContextMemories instead */
  getContextMemories: (scope: string, scopeId: string) =>
    analyticsClient.get<{ memories: ContextDocument[]; count: number }>(
      `/api/v1/context/memory/${encodeURIComponent(scope)}/${encodeURIComponent(scopeId)}`,
    ),

  triggerContextMaintenance: (projectId?: string) =>
    analyticsClient.post<MaintenanceReport>(
      '/api/v1/context/lifecycle/maintenance',
      { project_id: projectId ?? null },
    ),

  listContextConflicts: (scope?: string, scopeId?: string) => {
    const sp = new URLSearchParams();
    if (scope) sp.set('scope', scope);
    if (scopeId) sp.set('scope_id', scopeId);
    const qs = sp.toString();
    return analyticsClient.get<{ conflicts: ContextConflict[]; count: number }>(
      `/api/v1/context/lifecycle/conflicts${qs ? `?${qs}` : ''}`,
    );
  },

  /** @deprecated Use listContextConflicts instead */
  getContextConflicts: () =>
    analyticsClient.get<{ conflicts: ContextConflict[]; count: number }>('/api/v1/context/lifecycle/conflicts'),

  resolveContextConflict: (keepChunkId: string, discardChunkId: string) =>
    analyticsClient.post<{ status: string }>(
      '/api/v1/context/lifecycle/conflicts/resolve',
      { keep_chunk_id: keepChunkId, discard_chunk_id: discardChunkId },
    ),

  // ─── Fleet management ───

  listFleetWorkers: (params?: { status?: string; pool?: string; org_id?: string }) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set('status', params.status);
    if (params?.pool) sp.set('pool', params.pool);
    if (params?.org_id) sp.set('org_id', params.org_id);
    const qs = sp.toString();
    return analyticsClient.get<{ workers: FleetWorker[]; total: number }>(
      `/api/v1/fleet/workers${qs ? `?${qs}` : ''}`,
    );
  },

  getFleetWorker: (workerId: string) =>
    analyticsClient.get<{ worker: FleetWorker }>(
      `/api/v1/fleet/workers/${encodeURIComponent(workerId)}`,
    ),

  approveFleetWorker: (workerId: string, approvedBy: string) =>
    analyticsClient.post<{ worker: FleetWorker }>(
      `/api/v1/fleet/workers/${encodeURIComponent(workerId)}/approve`,
      { approved_by: approvedBy },
    ),

  rejectFleetWorker: (workerId: string) =>
    analyticsClient.post<{ worker: FleetWorker }>(
      `/api/v1/fleet/workers/${encodeURIComponent(workerId)}/reject`,
      {},
    ),

  revokeFleetWorker: (workerId: string) =>
    analyticsClient.post<{ worker: FleetWorker }>(
      `/api/v1/fleet/workers/${encodeURIComponent(workerId)}/revoke`,
      {},
    ),

  listEnrollmentKeys: (orgId?: string) => {
    const sp = new URLSearchParams();
    if (orgId) sp.set('org_id', orgId);
    const qs = sp.toString();
    return analyticsClient.get<{ keys: FleetEnrollmentKey[]; total: number }>(
      `/api/v1/fleet/enrollment-keys${qs ? `?${qs}` : ''}`,
    );
  },

  createEnrollmentKey: (data: FleetEnrollmentKeyCreate, orgId?: string) => {
    const sp = new URLSearchParams();
    if (orgId) sp.set('org_id', orgId);
    const qs = sp.toString();
    return analyticsClient.post<FleetEnrollmentKey>(
      `/api/v1/fleet/enrollment-keys${qs ? `?${qs}` : ''}`,
      data,
    );
  },

  revokeEnrollmentKey: (keyId: string) =>
    analyticsClient.delete<{ status: string; key_id: string }>(
      `/api/v1/fleet/enrollment-keys/${encodeURIComponent(keyId)}`,
    ),

  listFleetAudit: (params?: {
    event_type?: string;
    worker_id?: string;
    limit?: number;
    org_id?: string;
  }) => {
    const sp = new URLSearchParams();
    if (params?.event_type) sp.set('event_type', params.event_type);
    if (params?.worker_id) sp.set('worker_id', params.worker_id);
    if (params?.limit) sp.set('limit', String(params.limit));
    if (params?.org_id) sp.set('org_id', params.org_id);
    const qs = sp.toString();
    return analyticsClient.get<{ events: FleetAuditEvent[]; total: number }>(
      `/api/v1/fleet/audit${qs ? `?${qs}` : ''}`,
    );
  },

  getWorkerPoolStats: async (workerId: string): Promise<PoolStatsSnapshot | null> => {
    const res = await fetch(
      `/api/v1/admin/fleet/workers/${encodeURIComponent(workerId)}/pool-stats`,
      { credentials: 'include' },
    );
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getWorkerPoolStats: ${res.status}`);
    const j = await res.json();
    if (j.snapshot === null || j.snapshot === undefined) return null;
    return j as PoolStatsSnapshot;
  },

  /* ─── Billing endpoints ─── */
  listBillingPlans: () =>
    analyticsClient.get<BillingPlan[]>('/api/v1/billing/plans'),

  getBillingSubscription: () =>
    analyticsClient.get<BillingSubscription>('/api/v1/billing/subscription'),

  getBillingUsage: () =>
    analyticsClient.get<BillingUsage>('/api/v1/billing/usage'),

  listBillingInvoices: () =>
    analyticsClient.get<BillingInvoice[]>('/api/v1/billing/invoices'),

  createCheckoutSession: (planId: string) =>
    analyticsClient.post<{ url: string; session_id: string }>(
      '/api/v1/billing/checkout',
      { plan_id: planId },
    ),

  createBillingPortal: () =>
    analyticsClient.post<{ url: string }>('/api/v1/billing/portal', {}),

  /* ─── Autopilot endpoints ─── */
  getAutopilotStatus: () =>
    analyticsClient.get<AutopilotStatus>('/api/v1/autopilot/status'),

  enableAutopilot: (tier: string) =>
    analyticsClient.post<AutopilotStatus>('/api/v1/autopilot/enable', { tier }),

  disableAutopilot: () =>
    analyticsClient.post<{ status: string }>('/api/v1/autopilot/disable', {}),

  getAutopilotSystemReadiness: () =>
    analyticsClient.get<{
      providers: Array<{
        id: string;
        provider_name: string;
        display_name: string;
        default: boolean;
        model: string | null;
        type: string;
      }>;
      default_provider: { id: string; provider_name: string; model: string | null } | null;
      search_keys_set: { serper: boolean; tavily: boolean; brave: boolean };
      active_search_backend: string;
      warnings: Array<{ code: string; severity: 'error' | 'warning' | 'info'; message: string; fix: string }>;
      ready: boolean;
    }>('/api/v1/autopilot/system-readiness'),

  submitAutopilotGoal: (goal: string) =>
    analyticsClient.post<AutopilotGoalResponse>('/api/v1/autopilot/goal', { goal }),

  synthesizeAutopilotBlueprint: (goal: string) =>
    analyticsClient.post<AutopilotGoalResponse>('/api/v1/autopilot/synthesize', { goal }),

  approveAutopilotMission: (missionId: string, blueprintId?: string) =>
    analyticsClient.post<{ status: string; mission_id: string }>(
      '/api/v1/autopilot/approve',
      { mission_id: missionId, blueprint_id: blueprintId ?? null },
    ),

  listAutopilotMissions: (
    limitOrOpts?: number | { limit?: number; offset?: number; q?: string; status?: string },
  ) => {
    const opts =
      typeof limitOrOpts === "number"
        ? { limit: limitOrOpts }
        : limitOrOpts ?? {};
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.offset !== undefined) params.set("offset", String(opts.offset));
    if (opts.q) params.set("q", opts.q);
    if (opts.status) params.set("status", opts.status);
    const qs = params.toString() ? `?${params.toString()}` : "";
    return analyticsClient.get<AutopilotMissionsResponse>(
      `/api/v1/autopilot/missions${qs}`,
    );
  },

  rerunAutopilotMission: (missionId: string) =>
    analyticsClient.post<{ mission_id: string; source_mission_id: string; status: string }>(
      `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/rerun`,
      {},
    ),

  cancelAutopilotMission: (missionId: string, reason: string) =>
    analyticsClient.post<{ mission_id: string; status: string }>(
      `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/cancel`,
      { reason },
    ),

  explainBlueprint: (blueprint_json: object) =>
    analyticsClient.post<BlueprintExplainResponse>('/v1/blueprints/explain', { blueprint_json }),

  /* ─── Sandbox config endpoints (Plan 3b-i) ─── */
  getProjectSandboxDefaults: async (slug: string): Promise<SandboxRequirementsResponse | null> => {
    const res = await fetch(`/api/v1/admin/projects/${encodeURIComponent(slug)}/sandbox-defaults`, {
      credentials: 'include',
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getProjectSandboxDefaults: ${res.status}`);
    return res.json();
  },

  putProjectSandboxDefaults: async (
    slug: string, payload: SandboxRequirementsPayload
  ): Promise<SandboxRequirementsResponse> => {
    const res = await fetch(`/api/v1/admin/projects/${encodeURIComponent(slug)}/sandbox-defaults`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`putProjectSandboxDefaults: ${res.status}`);
    return res.json();
  },

  deleteProjectSandboxDefaults: async (slug: string): Promise<void> => {
    const res = await fetch(`/api/v1/admin/projects/${encodeURIComponent(slug)}/sandbox-defaults`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok && res.status !== 204) {
      throw new Error(`deleteProjectSandboxDefaults: ${res.status}`);
    }
  },

  getAgentSandboxRequirements: async (name: string): Promise<SandboxRequirementsResponse | null> => {
    const res = await fetch(`/api/v1/admin/agents/${encodeURIComponent(name)}/sandbox-requirements`, {
      credentials: 'include',
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getAgentSandboxRequirements: ${res.status}`);
    return res.json();
  },

  putAgentSandboxRequirements: async (
    name: string, payload: SandboxRequirementsPayload
  ): Promise<SandboxRequirementsResponse> => {
    const res = await fetch(`/api/v1/admin/agents/${encodeURIComponent(name)}/sandbox-requirements`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`putAgentSandboxRequirements: ${res.status}`);
    return res.json();
  },

  deleteAgentSandboxRequirements: async (name: string): Promise<void> => {
    const res = await fetch(`/api/v1/admin/agents/${encodeURIComponent(name)}/sandbox-requirements`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok && res.status !== 204) {
      throw new Error(`deleteAgentSandboxRequirements: ${res.status}`);
    }
  },

  getSandboxResolutionPreview: async (query: {
    project?: string;
    agent?: string;
    draft?: Partial<SandboxRequirementsPayload>;
  }): Promise<SandboxResolutionPreview> => {
    const params = new URLSearchParams();
    if (query.project) params.set('project', query.project);
    if (query.agent) params.set('agent', query.agent);
    if (query.draft?.sandbox_mode) params.set('draft_mode', query.draft.sandbox_mode);
    if (query.draft?.image) params.set('draft_image', query.draft.image);
    if (query.draft?.network_policy) params.set('draft_network_policy', query.draft.network_policy);
    const res = await fetch(`/api/v1/admin/sandbox/preview?${params}`, {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`getSandboxResolutionPreview: ${res.status}`);
    return res.json();
  },

  /* ─── Sealed-i endpoints ─── */

  getSealedStatus: async (): Promise<SealedStatus> => {
    const res = await fetch('/api/v1/admin/sealed/status', { credentials: 'include' });
    if (!res.ok) throw new Error(`getSealedStatus: ${res.status}`);
    return res.json();
  },

  listProfiles: async (): Promise<ProfileMetadata[]> => {
    const res = await fetch('/api/v1/admin/sealed/profiles', { credentials: 'include' });
    if (!res.ok) throw new Error(`listProfiles: ${res.status}`);
    return res.json();
  },

  getProfile: async (id: string): Promise<ProfileMetadata | null> => {
    const res = await fetch(`/api/v1/admin/sealed/profiles/${encodeURIComponent(id)}`, {
      credentials: 'include',
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getProfile: ${res.status}`);
    return res.json();
  },

  getProfileFull: async (id: string): Promise<Profile | null> => {
    const res = await fetch(`/api/v1/admin/sealed/profiles/${encodeURIComponent(id)}/full`, {
      credentials: 'include',
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getProfileFull: ${res.status}`);
    return res.json();
  },

  createProfile: async (payload: ProfileWritePayload): Promise<Profile> => {
    const res = await fetch('/api/v1/admin/sealed/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`createProfile: ${res.status}`);
    return res.json();
  },

  updateSealedProfile: async (id: string, payload: ProfileWritePayload): Promise<Profile> => {
    const res = await fetch(`/api/v1/admin/sealed/profiles/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`updateSealedProfile: ${res.status}`);
    return res.json();
  },

  deleteProfile: async (id: string): Promise<void> => {
    const res = await fetch(`/api/v1/admin/sealed/profiles/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok && res.status !== 204) throw new Error(`deleteProfile: ${res.status}`);
  },

  revealSecret: async (profileId: string, secretKey: string): Promise<{ value: string }> => {
    const res = await fetch(
      `/api/v1/admin/sealed/profiles/${encodeURIComponent(profileId)}/reveal/${encodeURIComponent(secretKey)}`,
      { method: 'POST', credentials: 'include' },
    );
    if (!res.ok) throw new Error(`revealSecret: ${res.status}`);
    return res.json();
  },

  getSealedSystem: async (): Promise<SealedSystemConfig> => {
    const res = await fetch('/api/v1/admin/sealed/system', { credentials: 'include' });
    if (!res.ok) throw new Error(`getSealedSystem: ${res.status}`);
    return res.json();
  },

  putSealedSystem: async (payload: SealedSystemConfig): Promise<SealedSystemConfig> => {
    const res = await fetch('/api/v1/admin/sealed/system', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`putSealedSystem: ${res.status}`);
    return res.json();
  },

  getSealedWorkflow: async (name: string): Promise<SealedWorkflowConfig | null> => {
    const res = await fetch(
      `/api/v1/admin/sealed/workflows/${encodeURIComponent(name)}`,
      { credentials: 'include' },
    );
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getSealedWorkflow: ${res.status}`);
    return res.json();
  },

  putSealedWorkflow: async (name: string, payload: SealedWorkflowConfig): Promise<SealedWorkflowConfig> => {
    const res = await fetch(`/api/v1/admin/sealed/workflows/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`putSealedWorkflow: ${res.status}`);
    return res.json();
  },

  getSealedPreview: async (query: {
    project?: string;
    workflow?: string;
    user_profile_ref?: string;
    user_overrides?: Record<string, string>;
  }): Promise<EffectiveProfile> => {
    const params = new URLSearchParams();
    if (query.project) params.set('project', query.project);
    if (query.workflow) params.set('workflow', query.workflow);
    if (query.user_profile_ref) params.set('user_profile_ref', query.user_profile_ref);
    if (query.user_overrides) params.set('user_overrides_json', JSON.stringify(query.user_overrides));
    const res = await fetch(`/api/v1/admin/sealed/preview?${params}`, { credentials: 'include' });
    if (!res.ok) throw new Error(`getSealedPreview: ${res.status}`);
    return res.json();
  },

  getSealedAudit: async (query: {
    profile_id?: string;
    event_type?: string;
    actor_id?: string;
    since?: string;
    until?: string;
    limit?: number;
  }): Promise<SealedAuditEvent[]> => {
    const params = new URLSearchParams();
    if (query.profile_id) params.set('profile_id', query.profile_id);
    if (query.event_type) params.set('event_type', query.event_type);
    if (query.actor_id) params.set('actor_id', query.actor_id);
    if (query.since) params.set('since', query.since);
    if (query.until) params.set('until', query.until);
    if (query.limit != null) params.set('limit', String(query.limit));
    const qs = params.toString();
    const res = await fetch(`/api/v1/admin/sealed/audit${qs ? `?${qs}` : ''}`, {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`getSealedAudit: ${res.status}`);
    return res.json();
  },

  /* ─── Sealed-iii.A — revocation endpoints ─── */

  listRevocations: async (query: {
    profile_id?: string;
    include_lifted?: boolean;
    limit?: number;
  } = {}): Promise<Revocation[]> => {
    const params = new URLSearchParams();
    if (query.profile_id) params.set('profile_id', query.profile_id);
    if (query.include_lifted) params.set('include_lifted', 'true');
    if (query.limit) params.set('limit', String(query.limit));
    const qs = params.toString();
    const res = await fetch(
      `/api/v1/admin/sealed/revocations${qs ? `?${qs}` : ''}`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`listRevocations: ${res.status}`);
    return res.json();
  },

  revokeSecret: async (payload: {
    profile_id: string;
    secret_key: string | null;
    reason: string;
    hard?: boolean;
    current_keys?: string[];
  }): Promise<{ revocations: Revocation[]; affected_runs: string[] }> => {
    const res = await fetch('/api/v1/admin/sealed/revocations', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(`revokeSecret: ${res.status} ${JSON.stringify(err)}`);
    }
    return res.json();
  },

  liftRevocation: async (id: number): Promise<Revocation> => {
    const res = await fetch(`/api/v1/admin/sealed/revocations/${id}`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(`liftRevocation: ${res.status} ${JSON.stringify(err)}`);
    }
    return res.json();
  },

  previewRevoke: async (
    profile_id: string,
    secret_key: string | null,
  ): Promise<{ affected_runs: string[] }> => {
    const params = new URLSearchParams({ profile_id });
    if (secret_key) params.set('secret_key', secret_key);
    const res = await fetch(
      `/api/v1/admin/sealed/revocations/preview?${params.toString()}`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`previewRevoke: ${res.status}`);
    return res.json();
  },

  // ── Plan ART — artifact destinations ──────────────────────────────
  getWorkflowArtifactDestination: async (
    name: string,
  ): Promise<ArtifactDestination | null> => {
    const res = await fetch(
      `/api/v1/admin/workflows/${encodeURIComponent(name)}/artifact_destination`,
      { credentials: 'include' },
    );
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`getWorkflowArtifactDestination: ${res.status}`);
    return res.json();
  },

  putWorkflowArtifactDestination: async (
    name: string,
    payload: ArtifactDestination,
  ): Promise<ArtifactDestination> => {
    const res = await fetch(
      `/api/v1/admin/workflows/${encodeURIComponent(name)}/artifact_destination`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'include',
      },
    );
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`putWorkflowArtifactDestination: ${res.status} ${body}`);
    }
    return res.json();
  },

  deleteWorkflowArtifactDestination: async (name: string): Promise<void> => {
    const res = await fetch(
      `/api/v1/admin/workflows/${encodeURIComponent(name)}/artifact_destination`,
      { method: 'DELETE', credentials: 'include' },
    );
    if (!res.ok && res.status !== 204) {
      throw new Error(`deleteWorkflowArtifactDestination: ${res.status}`);
    }
  },

  /* ─── Sealed-iii.C replay ─── */

  async previewReplay(runId: string, fromStep: number): Promise<ReplayPreview> {
    const res = await fetch(
      `/api/v1/admin/workflows/runs/${encodeURIComponent(runId)}/replay/preview`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ from_step: fromStep }),
      },
    );
    if (!res.ok) throw new Error(`previewReplay: ${res.status}`);
    return res.json();
  },

  async commitReplay(
    runId: string,
    fromStep: number,
    confirmWarnings = false,
  ): Promise<ReplayCommitResult> {
    const res = await fetch(
      `/api/v1/admin/workflows/runs/${encodeURIComponent(runId)}/replay`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          from_step: fromStep,
          confirm_warnings: confirmWarnings,
        }),
      },
    );
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`commitReplay: ${res.status} ${detail}`);
    }
    return res.json();
  },

  async listReplaysOf(runId: string): Promise<{ replays: ReplayInfo[] }> {
    const res = await fetch(
      `/api/v1/admin/workflows/runs/${encodeURIComponent(runId)}/replays`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`listReplaysOf: ${res.status}`);
    return res.json();
  },

  /* ─── Sealed-v — reactive directives ─── */

  async getDirectivePolicies(): Promise<DirectivesConfig> {
    const res = await fetch('/api/v1/admin/directives/policies', {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`getDirectivePolicies: ${res.status}`);
    return res.json();
  },

  async putDirectivePolicies(cfg: DirectivesConfig): Promise<{ ok: boolean }> {
    const res = await fetch('/api/v1/admin/directives/policies', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`putDirectivePolicies: ${res.status} ${detail}`);
    }
    return res.json();
  },

  async previewDirectivesForWorkflow(
    workflow: string,
    projectId?: string,
  ): Promise<{ active_policies: DirectivePolicy[] }> {
    const sp = new URLSearchParams({ workflow });
    if (projectId) sp.set('project_id', projectId);
    const res = await fetch(
      `/api/v1/admin/directives/preview?${sp.toString()}`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`previewDirectivesForWorkflow: ${res.status}`);
    return res.json();
  },

  async listDirectiveEvaluations(params: {
    run_id?: string;
    policy_id?: string;
    event_type?: string;
    limit?: number;
  }): Promise<{ events: DirectiveEvaluation[] }> {
    const sp = new URLSearchParams();
    if (params.run_id) sp.set('run_id', params.run_id);
    if (params.policy_id) sp.set('policy_id', params.policy_id);
    if (params.event_type) sp.set('event_type', params.event_type);
    if (params.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    const res = await fetch(
      `/api/v1/admin/directives/evaluations${qs ? `?${qs}` : ''}`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`listDirectiveEvaluations: ${res.status}`);
    return res.json();
  },

  async listDirectiveApprovals(): Promise<{ pending: PendingApproval[] }> {
    const res = await fetch('/api/v1/admin/directives/approvals', {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`listDirectiveApprovals: ${res.status}`);
    return res.json();
  },

  async approveDirective(
    decisionId: string,
    actor: string,
    note: string,
  ): Promise<unknown> {
    const res = await fetch(
      `/api/v1/admin/directives/approvals/${encodeURIComponent(decisionId)}/approve`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ actor, note }),
      },
    );
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`approveDirective: ${res.status} ${detail}`);
    }
    return res.json();
  },

  async denyDirective(
    decisionId: string,
    actor: string,
    note: string,
  ): Promise<unknown> {
    const res = await fetch(
      `/api/v1/admin/directives/approvals/${encodeURIComponent(decisionId)}/deny`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ actor, note }),
      },
    );
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`denyDirective: ${res.status} ${detail}`);
    }
    return res.json();
  },

  async getRunDirectiveSummary(
    runId: string,
  ): Promise<{ events: DirectiveEvaluation[] }> {
    const res = await fetch(
      `/api/v1/admin/directives/runs/${encodeURIComponent(runId)}`,
      { credentials: 'include' },
    );
    if (!res.ok) throw new Error(`getRunDirectiveSummary: ${res.status}`);
    return res.json();
  },

  /* ─── Inference providers / connections (Gap #10) ─── */
  getInferenceProviderCatalog: () =>
    analyticsClient.get<InferenceProviderCatalog>(
      '/api/v1/admin/connections/catalog',
    ),

  listInferenceProviders: () =>
    analyticsClient.get<InferenceProviderMetadata[]>(
      '/api/v1/admin/connections',
    ),

  getInferenceProvider: (provider: InferenceProviderKey) =>
    analyticsClient.get<InferenceProviderMetadata>(
      `/api/v1/admin/connections/${encodeURIComponent(provider)}`,
    ),

  upsertInferenceProvider: (
    provider: InferenceProviderKey,
    payload: InferenceProviderWritePayload,
  ) =>
    analyticsClient.put<InferenceProviderMetadata>(
      `/api/v1/admin/connections/${encodeURIComponent(provider)}`,
      payload,
    ),

  deleteInferenceProvider: (provider: InferenceProviderKey) =>
    analyticsClient.delete<void>(
      `/api/v1/admin/connections/${encodeURIComponent(provider)}`,
    ),

  testInferenceProvider: (provider: InferenceProviderKey) =>
    analyticsClient.post<InferenceProviderTestResult>(
      `/api/v1/admin/connections/${encodeURIComponent(provider)}/test`,
      {},
    ),

  /* ─── Autopilot mission detail (Plan G) ─── */
  getAutopilotMission: (id: string) =>
    analyticsClient.get<AutopilotMissionDetail>(
      `/api/v1/autopilot/missions/${encodeURIComponent(id)}`,
    ),

  explainAutopilotMission: (id: string) =>
    analyticsClient.post<AutopilotMissionExplain>(
      `/api/v1/autopilot/missions/${encodeURIComponent(id)}/explain`,
      {},
    ),

  runAutopilotMission: (id: string) =>
    analyticsClient.post<{ run_id: string; started_at: string }>(
      `/api/v1/autopilot/missions/${encodeURIComponent(id)}/run`,
      {},
    ),

  /* ─── Autopilot mission trace (Plan H) ─── */
  getAutopilotMissionTrace: (id: string) =>
    analyticsClient.get<AutopilotMissionTrace>(
      `/api/v1/autopilot/missions/${encodeURIComponent(id)}/trace`,
    ),

  /* ─── Tool connections (batch-2a) ─── */
  listToolRegistry: () =>
    analyticsClient.get<ToolRegistryEntry[]>(
      '/api/v1/admin/connections/tools/registry',
    ),

  listToolConnections: () =>
    analyticsClient.get<ToolConnectionMetadata[]>(
      '/api/v1/admin/connections/tools',
    ),

  upsertToolConnection: (
    toolId: string,
    credentials: Record<string, string>,
  ) =>
    analyticsClient.put<ToolConnectionMetadata>(
      `/api/v1/admin/connections/tools/${encodeURIComponent(toolId)}`,
      { credentials },
    ),

  deleteToolConnection: (toolId: string) =>
    analyticsClient.delete<void>(
      `/api/v1/admin/connections/tools/${encodeURIComponent(toolId)}`,
    ),

  testToolConnection: (toolId: string) =>
    analyticsClient.post<ToolTestResult>(
      `/api/v1/admin/connections/tools/${encodeURIComponent(toolId)}/test`,
      {},
    ),

  // Legacy adminApi.oauthClients was deleted by Connections Platform PR5.
  // Use adminApi.connections (filtered by protocol="oauth2") instead.
};
