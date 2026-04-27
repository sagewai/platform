/** Admin dashboard type definitions — mirrors sagewai.admin.models */

/* ─── Cursor pagination ─── */

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

/* ─── Analytics types ─── */

export interface CostAnalytics {
  total_cost_usd: number;
  by_model: Record<string, number>;
  by_agent: Record<string, number>;
  record_count: number;
}

export interface UsageAnalytics {
  total_tokens: number;
  by_model: Record<string, number>;
  by_agent: Record<string, number>;
  record_count: number;
}

export interface RiskAnalytics {
  pii_events: number;
  hallucination_flags: number;
  content_filter_events: number;
  total_events: number;
}

export interface ModelAnalytics {
  model: string;
  total_cost_usd: number;
  total_tokens: number;
  request_count: number;
  cost_per_1k_tokens: number;
  is_local?: boolean;
}

export interface AgentAnalytics {
  agent_name: string;
  total_cost_usd: number;
  total_tokens: number;
  request_count: number;
  models_used: string[];
}

/* ─── Budget types ─── */

export interface BudgetLimit {
  agent_name: string;
  max_daily_usd: number;
  max_monthly_usd: number;
  action: 'warn' | 'throttle' | 'stop';
  fallback_chain: string[];
}

export interface BudgetStatus {
  agent_name: string;
  daily_spend_usd: number;
  monthly_spend_usd: number;
  max_daily_usd: number | null;
  max_monthly_usd: number | null;
  daily_remaining_usd: number | null;
  monthly_remaining_usd: number | null;
}

/* ─── Agent / Run / Session types ─── */

export interface AgentSummary {
  name: string;
  capabilities: string[];
  model: string;
  status: string;
  source: 'registered' | 'playground';
  strategy: string;
  tags: string[];
}

export interface AgentDetail {
  name: string;
  capabilities: string[];
  model: string;
  system_prompt: string;
  max_iterations: number;
  temperature?: number;
  top_p?: number | null;
  max_tokens?: number | null;
  frequency_penalty?: number | null;
  presence_penalty?: number | null;
  preset?: string | null;
  tools: string[];
  mcp_servers: string[];
  memory_backends: string[];
  guardrails: string[];
  status: string;
  source: 'registered' | 'playground';
  strategy: string;
  tags: string[];
  fallback_models: string[];
  total_runs: number;
  sandbox_requirements_override?: SandboxRequirementsResponse | null;
  sandbox_requirements_blueprint?: SandboxRequirementsResponse | null;
}

export interface RunSummary {
  run_id: string;
  agent_name: string;
  status: string;
  input_preview: string;
  output_preview: string;
  started_at: number | null;
  completed_at: number | null;
  total_tokens: number;
  run_type: 'standalone' | 'workflow_step' | 'directive_delegation';
  parent_workflow_run_id: string | null;
}

export interface RunDetail {
  run_id: string;
  agent_name: string;
  status: string;
  input_text: string;
  output_text: string;
  started_at: number | null;
  completed_at: number | null;
  total_tokens: number;
  tool_calls: ToolCallRecord[];
  steps: StepInfo[];
}

export interface ToolCallRecord {
  tool_name: string;
  arguments: string;
  result_preview: string;
  duration_ms: number;
}

export interface StepInfo {
  step_type: string;
  detail: string;
  duration_ms: number;
}

export interface SessionInfo {
  session_id: string;
  agent_name: string;
  started_at: number;
  message_count: number;
  status: string;
}

/* ─── Guardrails types ─── */

export interface GuardrailConfig {
  id: number;
  agent_name: string;
  guardrail_type: 'pii' | 'hallucination' | 'content_filter';
  enabled: boolean;
  config: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

/* ─── Audit types ─── */

export interface AuditEvent {
  id: number;
  agent_name: string;
  event_type: string;
  entity_type: string | null;
  action: string | null;
  detail: string | null;
  created_at: string;
}

export interface AuditEventsResponse {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

/* ─── Memory types ─── */

export interface VectorStats {
  status: string;
  documents: number;
  backend: string;
}

export interface VectorSearchResult {
  content: string;
  rank: number;
}

export interface VectorSearchResponse {
  query: string;
  results: VectorSearchResult[];
  count: number;
}

export interface GraphStats {
  status: string;
  entities: number;
  relations: number;
  backend: string;
}

export interface GraphSearchResponse {
  query: string;
  results: { content: string; rank: number }[];
  count: number;
}

export interface GraphEntity {
  name: string;
  metadata: Record<string, unknown>;
}

/* ─── Eval types ─── */

export interface EvalDatasetSummary {
  id: number;
  name: string;
  description: string | null;
  case_count: number;
  created_at: string | null;
}

export interface EvalCaseData {
  input: string;
  agent_name: string;
  criteria: string[];
  expected_output: string | null;
  metadata: Record<string, unknown> | null;
}

export interface EvalDatasetDetail extends EvalDatasetSummary {
  cases: EvalCaseData[];
}

export interface EvalRunSummary {
  id: number;
  dataset_id: number;
  agent_name: string;
  model: string;
  total_cases: number;
  passed: number;
  pass_rate: number;
  created_at: string | null;
}

export interface EvalScore {
  passed: boolean;
  score: number;
  reasoning: string;
  criteria_scores: Record<string, number>;
}

export interface EvalRunDetail extends EvalRunSummary {
  summary: {
    total: number;
    passed: number;
    failed: number;
    pass_rate: number;
    avg_score: number;
  };
  scores: EvalScore[];
}

/* ─── MCP types ─── */

export interface McpServer {
  name: string;
  path: string;
  status: string;
}

export interface McpTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface McpDiscoverResponse {
  server_cmd: string;
  tools: McpTool[];
  tool_count: number;
}

export interface McpCallResponse {
  tool_name: string;
  arguments: Record<string, unknown>;
  result: unknown;
}

/* ─── MCP Service Config types (legacy) ─── */

export interface McpServiceConfig {
  id: string;
  service_name: string;
  display_name: string;
  category: string;
  config: Record<string, string>;
  status: string;
  env_vars: Record<string, string>;
  env_vars_set: Record<string, boolean>;
}

export interface McpServiceTestResult {
  connected: boolean;
  latency_ms: number;
  tools?: string[];
  tool_count?: number;
  error?: string;
}

/* ─── Connector Catalog types ─── */

export interface ConnectorAuthField {
  key: string;
  label: string;
  env_var: string;
  secret: boolean;
  hint: string;
}

export interface ConnectorCatalogItem {
  name: string;
  display_name: string;
  category: string;
  description: string;
  auth_type: string;
  auth_fields: ConnectorAuthField[];
  docs_url: string | null;
  agent_description: string;
  example_prompt: string;
  supports_webhook: boolean;
  supports_listener: boolean;
  supports_poller: boolean;
  connected: boolean;
  is_custom: boolean;
  oauth_authorize_url?: string;
  oauth_token_url?: string;
  oauth_scopes?: string[];
}

export interface ConnectorHealthResult {
  status: string;
  latency_ms: number | null;
  tool_count: number | null;
  error: string | null;
  last_check: string | null;
}

export interface ConnectorTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface CustomConnectorRequest {
  name: string;
  display_name: string;
  description?: string;
  category?: string;
  mcp_command: string[];
  auth_type?: string;
  auth_fields?: ConnectorAuthField[];
  docs_url?: string;
  agent_description?: string;
  example_prompt?: string;
  oauth_authorize_url?: string;
  oauth_token_url?: string;
  oauth_scopes?: string[];
  supports_webhook?: boolean;
  supports_listener?: boolean;
  supports_poller?: boolean;
}

/* ─── Trigger types ─── */

export interface TriggerEventFilter {
  channels?: string[];
  event_types?: string[];
  senders?: string[];
  keywords?: string[];
}

export interface Trigger {
  id: string;
  source: string;
  strategy: string;
  poll_interval_seconds: number | null;
  filter: TriggerEventFilter;
  target: string;
  action: string;
  context: Record<string, unknown>;
  enabled: boolean;
}

export interface CreateTriggerRequest {
  source: string;
  strategy: string;
  poll_interval_seconds?: number;
  filter?: TriggerEventFilter;
  target: string;
  action?: string;
  context?: Record<string, unknown>;
}

/* ─── Model Router types ─── */

export interface RoutingRule {
  name: string;
  description: string;
  target_model: string;
  condition: string;
}

export interface RouteTestResponse {
  query: string;
  context: Record<string, unknown>;
  selected_model: string;
  default_model: string;
}

export interface AvailableModel {
  id: string;
  provider: string;
  supports_tools?: boolean;
  /** Custom API base URL (e.g. LM Studio local endpoint) */
  api_base?: string;
}

/* ─── Prompt Store types ─── */

export interface PromptLogSummary {
  log_id: string;
  run_id: string;
  agent_name: string;
  model: string;
  step_index: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number;
  strategy: string;
  created_at: number;
  is_example: boolean;
  tags: string[];
  source: 'playground' | 'workflow' | 'api';
  input_text: string;
  output_text: string;
}

export interface PromptLogDetail extends PromptLogSummary {
  prompt_messages: Array<{ role: string; content: string }>;
  response_message: { role: string; content: string };
  metadata: Record<string, unknown>;
}

export interface ReplayResponse {
  original_model: string;
  replay_model: string;
  original_response: { role: string; content: string };
  replay_response: { role: string; content: string };
  prompt_messages: Array<{ role: string; content: string }>;
}

/* ─── Token types ─── */

export interface TokenInfo {
  token_id: string;
  token_suffix?: string; // last 4 chars of the actual secret — populated by backend (#288)
  agent_name: string;
  grantor_id: string;
  scopes: string[];
  status: string;
  single_use: boolean;
  created_at: number;
  expires_at: number;
}

export interface CreateTokenResponse {
  token: string;
  token_id: string;
  agent_name: string;
  scopes: string[];
  expires_in_seconds: number;
}

/* ─── Session Detail types ─── */

export interface SessionDetailMessage {
  role: string;
  content: string;
  timestamp: string | null;
  token_count: number | null;
  tool_calls: Array<Record<string, unknown>> | null;
}

export interface SessionMessagesResponse {
  session_id: string;
  agent_name: string;
  messages: SessionDetailMessage[];
  total_messages: number;
  created_at: string | null;
  updated_at: string | null;
}

/* ─── Saved Workflow Registry types ─── */

export interface SavedWorkflow {
  id: string;
  project_id: string;
  name: string;
  description: string;
  yaml_content: string;
  version: number;
  is_active: boolean;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

/* ─── Workflow Run types (durable execution) ─── */

export type WorkflowRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface WorkflowRun {
  id: string;
  workflow_name: string;
  run_id: string;
  status: WorkflowRunStatus;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string | null;
  steps_completed: number;
  steps_total: number | null;
  data?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkflowEvent {
  id: number;
  run_id: string;
  event_type: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface WorkflowSubmitResponse {
  run_id: string;
  is_new: boolean;
  workflow_name: string;
  steps_total: number;
}

/** @deprecated Use WorkflowRun instead */
export interface WorkflowRunSummary {
  id: string;
  workflow_name: string;
  run_id: string;
  status: string;
  created_at: string;
  updated_at: string;
}

/** @deprecated Use WorkflowRun instead */
export interface WorkflowRunDetail extends WorkflowRunSummary {
  data: Record<string, unknown>;
}

/* ─── Workflow Template types ─── */

export interface WorkflowTemplate {
  name: string;
  description: string;
  yaml?: string;
  agents?: string[];
}

/* ─── System Health types ─── */

export interface ServiceHealth {
  name: string;
  status: 'healthy' | 'unhealthy' | 'not_configured';
  latency_ms: number | null;
  detail: string | null;
}

export interface SystemHealth {
  status: 'healthy' | 'degraded' | 'unhealthy';
  services: ServiceHealth[];
  sdk_version: string;
  checked_at: string;
}

/* ─── Cloud Auth types ─── */

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
}

export interface AuthTokens {
  access_token: string;
  refresh_token?: string;  // Issued as httpOnly cookie; present in body for back-compat only
  token_type: string;
  user: AuthUser;
}

/* ─── Workspace types ─── */

export interface Organization {
  id: string;
  name: string;
  slug: string;
  owner_id: string;
}

export interface Workspace {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  settings: Record<string, unknown>;
  role?: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: 'owner' | 'admin' | 'member' | 'viewer';
  email: string;
  display_name: string;
  avatar_url: string | null;
}

export interface Invitation {
  id: string;
  workspace_id: string;
  email: string;
  role: string;
  invited_by: string;
}

/* ─── Setup types ─── */

export interface SetupStatus {
  setup_required: boolean;
  reason?: string;
}

export interface SetupRequest {
  org_name: string;
  org_slug: string;
  contact_email: string;
  timezone: string;
  app_name: string;
  app_description: string;
  admin_name: string;
  admin_email: string;
  admin_password: string;
}

export interface SetupResponse {
  ok: boolean;
  org_slug: string;
  app_slug: string;
  message: string;
}

/* ─── Project types ─── */

export interface Project {
  slug: string;
  name: string;
  environment: string;
  allowed_origins: string;
  default_model: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

/* ─── Agent Template types ─── */

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  temperature: number;
  strategy: string;
  tools: string[];
  mcp_servers: string[];
  memory_backends: string[];
  guardrails: string[];
  category: string;
}

export interface CapabilityItem {
  id: string;
  name: string;
  description: string;
}

export interface AvailableCapabilities {
  tools: CapabilityItem[];
  mcp_servers: CapabilityItem[];
  memory: CapabilityItem[];
  guardrails: CapabilityItem[];
  strategies: CapabilityItem[];
}

/* ─── Self-Hosted Provider Config types ─── */

export interface ProviderConfig {
  id: string;
  provider_name: string;
  provider_type: string;
  display_name: string;
  config: Record<string, string>;
  status: string;
  env_var_key: string;
  env_var_set: boolean;
}

export interface SelfHostedProviderTestResult {
  connected: boolean;
  latency_ms: number;
  error?: string;
  models?: string[];
  note?: string;
}

export interface OllamaModelInfo {
  name: string;
  size: number;
  modified_at: string;
  parameter_size: string;
  quantization: string;
}

export interface LMStudioModelInfo {
  id: string;
  owned_by: string;
}

/* ─── LLM Provider types ─── */

export interface LLMProvider {
  id: number;
  workspace_id: string;
  provider_name: string;
  display_name: string;
  is_default: boolean;
  api_key_masked: string;
}

export interface ProviderTestResult {
  status: string;
  provider: string;
  detail: string;
}

/* ─── Settings: Organization + Account ─── */

export interface OrgSettings {
  org_name: string;
  org_slug: string;
  app_url: string;
  contact_email: string;
  timezone: string;
  industry: string;
  team_size: string;
  admin_email: string;
  completed_at: string;
  // Infrastructure settings
  litellm_proxy_url: string;
  litellm_api_key_set: boolean;
  milvus_uri: string;
  nebula_host: string;
  nebula_port: number;
}

export interface TestLiteLLMResponse {
  healthy: boolean;
  status?: number;
  error?: string;
  models: string[];
}

export interface AccountInfo {
  email: string;
  display_name: string;
  org_name: string;
}

/* ─── Save Prompt types ─── */

export interface SavePromptRequest {
  agent_name: string;
  model?: string;
  input_text: string;
  output_text: string;
  total_tokens?: number;
  tags?: string[];
  source?: string;
  is_example?: boolean;
}

export interface UpdatePromptRequest {
  tags?: string[];
  is_example?: boolean;
}

/* ─── Health Summary types ─── */

export interface ProviderStatus {
  name: string;
  configured: boolean;
}

export interface HealthSummary {
  providers: ProviderStatus[];
  databases: Array<{ name: string; status: string; latency_ms?: number }>;
  workers: { active: number; queued: number };
}

/* ─── Workflow Dashboard types ─── */

export interface QueueStats {
  pending: number;
  running: number;
  completed: number;
  failed: number;
  waiting: number;
  total: number;
}

export interface WorkerInfo {
  owner_id: string;
  active_runs: number;
  last_heartbeat: string;
}

export interface DLQEntry {
  id: number;
  run_id: string;
  workflow_name: string;
  error: string;
  retry_count: number;
  created_at: string;
}

// ── Context Engine types ─────────────────────────────────────────

export interface ContextDocument {
  id: string;
  scope: string;
  scope_id: string;
  project_id: string;
  title: string;
  source: string;
  source_uri: string | null;
  mime_type: string | null;
  file_size_bytes: number | null;
  chunk_count: number;
  status: string;
  confidence: number;
  freshness_at: string | null;
  metadata: Record<string, unknown>;
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface ContextChunk {
  id: string;
  document_id: string;
  scope: string;
  scope_id: string;
  content: string;
  chunk_index: number;
  token_count: number;
  content_hash: string;
  importance: number;
  access_count: number;
  last_accessed_at: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface ContextSearchResult {
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  scope: string;
  scope_id: string;
  document_title: string;
  source: string;
  metadata: Record<string, unknown>;
}

export interface ContextStats {
  status: string;
  documents: number;
  chunks: number;
  by_scope: Record<string, number>;
  by_source: Record<string, number>;
  by_status: Record<string, number>;
}

export interface ContextScopeInfo {
  scope: string;
  document_count: number;
  chunk_count: number;
}

export interface LifecycleReport {
  project_id: string;
  chunks_compressed: number;
  documents_archived: number;
  chunks_discarded: number;
  importance_refreshed: number;
  duration_ms: number;
}

/** Alias for LifecycleReport used by some API callers */
export type MaintenanceReport = LifecycleReport;

export interface ContextConflict {
  chunk_a_id: string;
  chunk_b_id: string;
  similarity: number;
  scope: string;
  scope_id: string;
  chunk_a_content: string;
  chunk_b_content: string;
}

/* ─── Fleet types ─── */

export type FleetWorkerStatus = 'pending' | 'approved' | 'rejected' | 'revoked';

export interface FleetWorkerCapabilities {
  models_supported: string[];
  models_canonical: string[];
  max_concurrent: number;
  labels: Record<string, string>;
  pool: string;
  sdk_version: string;
}

export interface FleetProbeResult {
  model: string;
  reachable: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface FleetAnomaly {
  type: string;
  message: string;
  detected_at: string;
}

export interface FleetWorker {
  id: string;
  name: string;
  org_id: string;
  capabilities: FleetWorkerCapabilities;
  approval_status: FleetWorkerStatus;
  last_heartbeat: string | null;
  last_probe_at: string | null;
  probe_status: string | null;
  registered_at: string;
  approved_at: string | null;
  approved_by: string | null;
  /** Enterprise: IP allowlist CIDRs */
  ip_allowlist?: string[];
  /** Enterprise: requires two-person approval */
  requires_dual_approval?: boolean;
  /** Enterprise: connection transport */
  connection_type?: 'http' | 'websocket';
  /** Enterprise: per-model probe results */
  probe_results?: FleetProbeResult[];
  /** Enterprise: detected anomalies */
  anomalies?: FleetAnomaly[];
}

export interface FleetEnrollmentKey {
  id: string;
  org_id: string;
  name: string;
  max_uses: number | null;
  current_uses: number;
  expires_at: string | null;
  allowed_pools: string[];
  allowed_models: string[];
  created_at: string;
  created_by: string;
  revoked: boolean;
  /** Only present on creation response */
  raw_key?: string;
}

export interface FleetEnrollmentKeyCreate {
  name: string;
  max_uses?: number;
  expires_at?: string;
  allowed_pools?: string[];
  allowed_models?: string[];
}

export interface FleetAuditEvent {
  id: string;
  org_id: string;
  event_type: string;
  worker_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

/* ─── Pool stats types (Plan 1.5) ─── */

export interface PerTupleStats {
  image_variant: string;
  execution_mode: string;
  network_policy: string;
  warm_count: number;
  warm_max: number;
  active_count: number;
  hit_rate_1h: number | null;
  last_evict_at: string | null;
  last_evict_reason: string | null;
}

export interface AggregateStats {
  warm_count: number;
  warm_max_global: number;
  active_count: number;
  hit_rate_1h: number | null;
  last_evict_at: string | null;
}

export interface PoolStatsSnapshot {
  worker_id: string;
  captured_at: string;
  per_tuple: PerTupleStats[];
  aggregate: AggregateStats;
}

/* ─── Billing types ─── */

export interface BillingPlan {
  id: string;
  name: string;
  price_monthly: number | null;
  stripe_price_id?: string;
  features: Record<string, number | boolean>;
}

export interface BillingSubscription {
  plan: string;
  status: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
}

export interface BillingUsage {
  period_start: string;
  period_end: string;
  agent_runs: number;
  api_calls: number;
  storage_used_gb: number;
  workers_active: number;
  connectors_active: number;
}

export interface BillingInvoice {
  id: string;
  date: string;
  amount: number;
  status: string;
  pdf_url: string;
}

/* ─── Autopilot ─── */

export type AutopilotTier = 'anonymous' | 'free' | 'custom';
export type AutopilotMissionStatus =
  | 'draft'
  | 'approved'
  | 'scheduled'
  | 'running'
  | 'completed'
  | 'failed';
export type AutopilotMode = 'scheduled' | 'event_driven' | 'batch';
export type AutopilotRoutingResult = 'auto_routed' | 'picker_needed' | 'synthesis_needed';

export interface AutopilotStatus {
  enabled: boolean;
  tier: AutopilotTier;
  quota_used: number;
  quota_limit: number | null;
  install_id: string | null;
}

export interface AutopilotSlot {
  key: string;
  value: string;
}

export interface AutopilotBlueprint {
  id: string;
  title: string;
  category: string;
  mode: AutopilotMode;
  slots: AutopilotSlot[];
  estimated_cost: string | null;
}

export interface AutopilotGoalResponse {
  routing_result: AutopilotRoutingResult;
  mission_id: string | null;
  blueprint: AutopilotBlueprint | null;
  candidates: AutopilotBlueprint[];
  message: string | null;
}

export interface AutopilotMissionStep {
  step: string;
  status: string;
  output: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AutopilotMission {
  id: string;
  blueprint_title: string;
  blueprint_category: string;
  status: AutopilotMissionStatus;
  mode: AutopilotMode;
  project_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  steps: AutopilotMissionStep[];
}

export interface AutopilotMissionsResponse {
  missions: AutopilotMission[];
  total: number;
}

// ── sandbox config (Plan 3b-i) ──────────────────────────────────────

export type SandboxModeValue = 'none' | 'per_tool' | 'per_run' | 'per_worker';
export type NetworkPolicyValue = 'none' | 'egress_allowlist' | 'full';
export type SandboxImageVariantValue =
  | 'base' | 'general' | 'ml' | 'ops' | 'erp' | 'ecommerce' | 'api';
export type SandboxResolutionOrigin =
  | 'explicit' | 'admin_override' | 'blueprint' | 'project_default' | 'sdk_default';

export interface SandboxRequirementsPayload {
  sandbox_mode: SandboxModeValue;
  image: string;
  network_policy: NetworkPolicyValue;
  required_secret_scopes: string[];
}

export interface SandboxRequirementsResponse extends SandboxRequirementsPayload {
  variant: SandboxImageVariantValue | null;
}

export interface SandboxResolutionField {
  value: string;
  origin: SandboxResolutionOrigin;
}

export interface SandboxResolutionPreview {
  sandbox_mode: SandboxResolutionField;
  image: SandboxResolutionField;
  variant: SandboxImageVariantValue | null;
  network_policy: SandboxResolutionField;
  resolved: SandboxRequirementsResponse;
}

// ── Sealed-i ─────────────────────────────────────────────────────────

export interface ProfileMetadata {
  id: string;
  name: string;
  description: string;
  owner: string | null;
  tags: string[];
  last_rotated_at: string | null;
  allowed_workflows: string[];
  env: Record<string, string>;
  secret_keys: string[];
}

export interface Profile extends ProfileMetadata {
  secrets: Record<string, string>;
}

export interface ProfileWritePayload {
  id?: string;
  name: string;
  description?: string;
  owner?: string;
  tags?: string[];
  allowed_workflows?: string[];
  env?: Record<string, string>;
  secrets?: Record<string, string>;
}

export interface SealedAuditEvent {
  id: number;
  event_type: string;
  actor_type: 'admin' | 'system' | 'runtime';
  actor_id: string | null;
  profile_id: string | null;
  secret_key: string | null;
  run_id: string | null;
  project_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface SealedStatus {
  master_key_configured: boolean;
  master_key_source: 'env-var' | 'keychain' | 'file' | 'none';
  master_key_last_rotated_at: string | null;
  audit_retention_days: number;
  reveal_rate_limit_per_admin_per_hour: number;
  backends_registered: string[];
}

export interface SealedSystemConfig {
  profile_ref: string | null;
  overrides: Record<string, string>;
}

export interface SealedWorkflowConfig {
  profile_ref: string | null;
  overrides: Record<string, string>;
}

export interface EffectiveProfile {
  env: Record<string, string>;
  secret_keys: string[];
  cascade_origins: Record<string, string>;
}

// ── Plan ART — artifact destinations ──────────────────────────────────

export type ArtifactDestinationType = 'github' | 's3' | 'local';

export interface ArtifactDestination {
  type: ArtifactDestinationType;
  target: string;
  env_keys: string[];
  options: Record<string, string>;
}

export interface ArtifactUploadResult {
  type: ArtifactDestinationType;
  target: string;
  bytes_uploaded: number;
  duration_ms: number;
  ref: string | null;
  object_count: number | null;
  warnings: string[];
}

// ── Sealed-iii.A — revocations ────────────────────────────────────────

export interface Revocation {
  id: number;
  profile_id: string;
  secret_key: string;
  revoked_at: string;
  revoked_by: string | null;
  reason: string;
  hard: boolean;
  lifted_at: string | null;
  lifted_by: string | null;
}
