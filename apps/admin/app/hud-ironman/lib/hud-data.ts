/**
 * HUD data model — the shape the graph, inspector, roster, and ticker consume.
 *
 * We map real Sagewai entities (AgentSummary/AgentDetail, Project, SavedWorkflow,
 * RunSummary) onto this richer model. Missing fields are synthesized
 * deterministically from the agent's name so the same agent looks identical
 * across page loads.
 *
 * The "actor" layer (vector store, MCP hub, guardrails, etc.) is purely
 * conceptual — it doesn't map to a single backend entity. We show a fixed
 * roster of actors with metrics sourced from available stats endpoints when
 * possible, otherwise deterministically seeded.
 */

export type HudRole =
  | 'orchestrator'
  | 'planner'
  | 'researcher'
  | 'coder'
  | 'reviewer'
  | 'tool'
  | 'memory'
  | 'evaluator'
  | 'sentinel';

export type HudStatus = 'active' | 'idle' | 'thinking' | 'alert';

export type HudActorKind = 'memory' | 'logs' | 'learning' | 'safety' | 'tools' | 'infra';
export type HudActorShape = 'ring' | 'slab' | 'diamond' | 'shield';

export interface HudProject {
  id: string;
  name: string;
  color: string;
  code: string;
  desc: string;
}

export interface HudAgent {
  id: string;
  name: string;
  role: HudRole;
  desc: string;
  model: string;
  temperature: number;
  topP: number;
  maxTokens: number;
  contextSize: number;
  tools: string[];
  skills: string[];
  status: HudStatus;
  uptime: number;
  calls24h: number;
  avgLatencyMs: number;
  p99LatencyMs: number;
  tokensPerSec: number;
  costToday: number;
  errorRate: number;
  version: string;
  systemPrompt: string;
  projects: string[];
  nodeClass: 'agent';
}

export interface HudActor {
  id: string;
  name: string;
  kind: HudActorKind;
  shape: HudActorShape;
  desc: string;
  status: HudStatus;
  version: string;
  throughput: number;
  errorRate: number;
  uptime: number;
  rows?: number;
  qps?: number;
  eventsPerSec?: number;
  retentionDays?: number;
  activeJobs?: number;
  queued?: number;
  blockedToday?: number;
  rules?: number;
  projects: string[];
  nodeClass: 'actor';
}

export interface HudLink {
  source: string;
  target: string;
  kind: string;
  volume: number;
}

export interface HudWorkflow {
  id: string;
  name: string;
  path: string[];
}

export interface HudRoleMeta {
  color: string;
  label: string;
  glyph: string;
}

export interface HudTickerEvent {
  tag: 'NOM' | 'ACK' | 'RX' | 'TX' | 'TOOL' | 'RAG' | 'EVAL' | 'WARN' | 'ERR';
  from: string;
  to: string;
  msg: string;
  ts: number;
}

export interface PlatformData {
  AGENTS: HudAgent[];
  ACTORS: HudActor[];
  LINKS: HudLink[];
  WORKFLOWS: HudWorkflow[];
  ROLES: Record<HudRole, HudRoleMeta>;
  PROJECTS: HudProject[];
}

/* ─── Static pools (conceptual, not from backend) ─── */

export const ROLES: Record<HudRole, HudRoleMeta> = {
  orchestrator: { color: '#ffb347', label: 'ORCHESTRATOR', glyph: '◆' },
  planner: { color: '#ffd56b', label: 'PLANNER', glyph: '◈' },
  researcher: { color: '#6be7ff', label: 'RESEARCHER', glyph: '◉' },
  coder: { color: '#9dffd0', label: 'CODER', glyph: '⬡' },
  reviewer: { color: '#c79bff', label: 'REVIEWER', glyph: '△' },
  tool: { color: '#7aa7ff', label: 'TOOL-USER', glyph: '⬢' },
  memory: { color: '#8ff0c2', label: 'MEMORY', glyph: '◎' },
  evaluator: { color: '#ff8fd1', label: 'EVALUATOR', glyph: '✕' },
  sentinel: { color: '#ff5a5a', label: 'SENTINEL', glyph: '⊗' },
};

export function roleColor(role: HudRole): string {
  const map: Record<HudRole, string> = {
    orchestrator: 'var(--amber)',
    planner: 'var(--amber)',
    researcher: 'var(--cyan)',
    coder: 'var(--green)',
    reviewer: 'var(--violet)',
    tool: 'var(--cyan)',
    memory: 'var(--green)',
    evaluator: '#ff8fd1',
    sentinel: 'var(--red)',
  };
  return map[role];
}

export function actorColor(kind: HudActorKind): string {
  const map: Record<HudActorKind, string> = {
    memory: 'var(--green)',
    logs: 'var(--violet)',
    learning: 'var(--amber)',
    safety: 'var(--red)',
    tools: 'var(--cyan)',
    infra: '#7fb7d6',
  };
  return map[kind];
}

const PROMPTS: Record<HudRole, string> = {
  orchestrator: `# ROLE
You are SAGEWAI, the primary orchestrator of a multi-agent platform.

# OBJECTIVE
Given a user goal, decompose it into sub-goals and dispatch them to the most
appropriate specialist agent. Maintain global state; resolve conflicts.

# CONSTRAINTS
- Never perform tool calls directly. Always delegate.
- Respect AEGIS policy envelope; halt on violation.
- Preserve citation chains end-to-end.

# OUTPUT
JSON envelope: { goal_id, dispatch: [{ agent, task, deadline }], rationale }`,

  planner: `# ROLE
Planner. Convert an objective into an ordered DAG of tasks.

# RULES
- Each task must be executable by exactly one specialist agent.
- Annotate dependencies; never create cycles.
- Prefer narrower, verifiable steps over broad generative ones.

# OUTPUT
Task graph (JSON) + human-readable plan summary.`,

  researcher: `# ROLE
Researcher. Retrieve, triangulate, cite.

# RULES
- Every factual claim MUST carry a citation.
- Prefer primary sources; deduplicate by canonical URL.
- Surface disagreement between sources explicitly.`,

  coder: `# ROLE
Coder. Author or patch code against a target repository.

# RULES
- Produce minimal diffs. No unrelated refactors.
- Run lints locally via code.lint before submitting.
- If tests exist, include updated tests. Never skip.`,

  reviewer: `# ROLE
Reviewer. Adversarial pass over produced artifacts.

# RULES
- Identify correctness, security, perf, and clarity issues.
- Rank findings P0→P3. P0/P1 block merge.
- Reject speculative "nice-to-haves" without concrete impact.`,

  tool: `# ROLE
Tool broker. Marshal external-tool requests from sibling agents.

# RULES
- Enforce per-tool rate limits and auth scopes.
- Log every call with request-id for AEGIS audit.
- Return structured errors; never raw stack traces.`,

  memory: `# ROLE
Episodic + semantic memory.

# RULES
- Upsert summaries, not raw transcripts, into vector store.
- TTL enforced per namespace; purge expired rows nightly.
- Return top-k with provenance on every recall.`,

  evaluator: `# ROLE
Evaluator. Score agent outputs against rubrics.

# RULES
- Use calibrated rubrics per task family.
- Emit JSON score object; include confidence interval.
- Flag regressions > 2σ vs rolling baseline.`,

  sentinel: `# ROLE
AEGIS — safety, policy, and PII sentinel.

# RULES
- Inspect every outbound artifact and tool call.
- Redact PII; halt on policy-class violations.
- Emit a signed envelope: { allow, redactions, reasons }.`,
};

const MODELS = [
  'claude-sonnet-4.5',
  'claude-opus-4.1',
  'claude-haiku-4.5',
  'gpt-4o',
  'gpt-4.1-mini',
  'llama-3.3-70b',
  'gemini-2.5-pro',
];

const TOOLS_POOL = [
  'web.search', 'web.fetch', 'code.exec', 'code.lint', 'repo.read', 'repo.commit',
  'db.query', 'db.write', 'shell', 'filesystem', 'k8s.deploy', 'pager.alert',
  'vector.search', 'vector.upsert', 'slack.post', 'jira.ticket', 'email.send',
  'browser.automate', 'calendar.read', 'pdf.parse', 'image.ocr', 'audio.transcribe',
];

const SKILLS_POOL = [
  'long-horizon-planning', 'chain-of-thought', 'self-critique', 'json-mode',
  'function-calling', 'multi-turn-memory', 'RAG', 'code-execution',
  'tool-selection', 'sub-task-decomposition', 'constrained-decoding',
  'formal-verification', 'rate-limiting', 'citation-grounding',
];

export const ALERT_POOL: ReadonlyArray<[HudTickerEvent['tag'], string]> = [
  ['NOM', 'nominal. handshake ok.'],
  ['ACK', 'task acknowledged.'],
  ['RX', 'message received from peer.'],
  ['TX', 'streaming tokens → downstream.'],
  ['TOOL', 'tool.invoke resolved.'],
  ['RAG', 'vector.search hit, k=8.'],
  ['EVAL', 'score Δ +0.04 vs baseline.'],
  ['WARN', 'retry 1/3 — upstream 429.'],
  ['WARN', 'ctx window pressure 87%.'],
  ['ERR', 'policy violation blocked.'],
  ['ERR', 'tool timeout (5s).'],
];

/* ─── Deterministic RNG (mulberry32) — seeded by agent id ─── */

function mulberry32(seed: number) {
  return function rng(): number {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seedFromString(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function pick<T>(rng: () => number, arr: readonly T[]): T {
  return arr[Math.floor(rng() * arr.length)];
}

function pickN<T>(rng: () => number, arr: readonly T[], n: number): T[] {
  const copy = [...arr];
  const out: T[] = [];
  for (let i = 0; i < n && copy.length; i++) {
    out.push(copy.splice(Math.floor(rng() * copy.length), 1)[0]);
  }
  return out;
}

/* ─── Role inference from agent name / capabilities / tags ─── */

export function inferRole(name: string, capabilities: string[] = [], tags: string[] = []): HudRole {
  const s = [name, ...capabilities, ...tags].join(' ').toLowerCase();
  if (/sage|orchestrat|routing/.test(s)) return 'orchestrator';
  if (/helios|plan|kairos|schedule/.test(s)) return 'planner';
  if (/oracle|research|scribe|search|retrieval/.test(s)) return 'researcher';
  if (/forge|vulcan|coder|code|build|deploy/.test(s)) return 'coder';
  if (/argus|nemesis|review|critic/.test(s)) return 'reviewer';
  if (/hermes|iris|tool|broker|mcp/.test(s)) return 'tool';
  if (/mnemos|memory|vector|recall/.test(s)) return 'memory';
  if (/themis|eval|score|judge/.test(s)) return 'evaluator';
  if (/aegis|sentinel|guard|safety|pii/.test(s)) return 'sentinel';
  return 'researcher';
}

/* ─── Synthesize a full HudAgent from partial real data ─── */

export interface PartialAgentInput {
  id: string; // stable identifier (agent name)
  name: string; // display name
  model?: string;
  systemPrompt?: string;
  temperature?: number | null;
  topP?: number | null;
  maxTokens?: number | null;
  tools?: string[];
  capabilities?: string[];
  tags?: string[];
  status?: string;
  totalRuns?: number;
  projects?: string[];
  desc?: string;
}

export function synthesizeAgent(input: PartialAgentInput): HudAgent {
  const rng = mulberry32(seedFromString(input.id));
  const role = inferRole(input.name, input.capabilities, input.tags);
  const maxTokens = input.maxTokens ?? [1024, 2048, 4096, 8192][Math.floor(rng() * 4)];

  let status: HudStatus;
  if (input.status === 'active' || input.status === 'running') status = 'active';
  else if (input.status === 'idle' || input.status === 'stopped') status = 'idle';
  else if (input.status === 'error' || input.status === 'alert') status = 'alert';
  else if (rng() < 0.78) status = 'active';
  else status = rng() < 0.5 ? 'idle' : 'thinking';
  if (role === 'sentinel' && rng() < 0.4) status = 'alert';

  return {
    id: input.id,
    name: input.name,
    role,
    desc: input.desc ?? defaultDesc(role, input.name),
    model: input.model || pick(rng, MODELS),
    temperature: input.temperature ?? +(0.1 + rng() * 0.8).toFixed(2),
    topP: input.topP ?? +(0.7 + rng() * 0.3).toFixed(2),
    maxTokens,
    contextSize: Math.floor(maxTokens * (0.2 + rng() * 0.7)),
    tools: input.tools && input.tools.length ? input.tools : pickN(rng, TOOLS_POOL, 3 + Math.floor(rng() * 4)),
    skills: pickN(rng, SKILLS_POOL, 3 + Math.floor(rng() * 3)),
    status,
    uptime: Math.floor(3600 + rng() * 86400 * 7),
    calls24h: input.totalRuns ?? Math.floor(120 + rng() * 4800),
    avgLatencyMs: Math.floor(180 + rng() * 1400),
    p99LatencyMs: 0, // filled below
    tokensPerSec: Math.floor(40 + rng() * 220),
    costToday: +(rng() * 42).toFixed(2),
    errorRate: +(rng() * 0.04).toFixed(3),
    version: `v${1 + Math.floor(rng() * 4)}.${Math.floor(rng() * 12)}.${Math.floor(rng() * 40)}`,
    systemPrompt: input.systemPrompt || PROMPTS[role],
    projects: input.projects && input.projects.length ? input.projects : ['prj-core'],
    nodeClass: 'agent',
  };
}

// Fill p99 once avgLatencyMs is known.
export function finalizeAgent(a: HudAgent): HudAgent {
  const rng = mulberry32(seedFromString(a.id) ^ 0xdeadbeef);
  return { ...a, p99LatencyMs: a.avgLatencyMs + Math.floor(400 + rng() * 1800) };
}

function defaultDesc(role: HudRole, name: string): string {
  switch (role) {
    case 'orchestrator': return `${name}: primary orchestrator. Routes high-level goals to sub-agents.`;
    case 'planner': return `${name}: decomposes objectives into ordered task graphs.`;
    case 'researcher': return `${name}: retrieves, triangulates, cites. Corpus-grounded.`;
    case 'coder': return `${name}: code generation, patch authoring, refactors.`;
    case 'reviewer': return `${name}: adversarial pass over produced artifacts.`;
    case 'tool': return `${name}: external-tool broker. Rate-limits and logs.`;
    case 'memory': return `${name}: episodic and semantic memory surface.`;
    case 'evaluator': return `${name}: scoring, calibration, regression checks.`;
    case 'sentinel': return `${name}: safety, policy, PII sentinel.`;
  }
}

/* ─── Actors — always synthesized; metrics injected from stats endpoints ─── */

export interface ActorMetricOverrides {
  vectorRows?: number;
  graphRows?: number;
  mcpCount?: number;
}

export function buildActors(
  allProjectIds: string[],
  overrides: ActorMetricOverrides = {},
): HudActor[] {
  const table: Array<Omit<HudActor, 'throughput' | 'errorRate' | 'uptime' | 'version' | 'status' | 'projects' | 'nodeClass'>> = [
    { id: 'VEC-STORE', name: 'VECTOR STORE', kind: 'memory', shape: 'ring', desc: 'High-dimensional memory. pgvector.' },
    { id: 'GRAPH-DB', name: 'KNOWLEDGE GRAPH', kind: 'memory', shape: 'ring', desc: 'Entities & relations graph store.' },
    { id: 'CORPUS', name: 'TRAINING CORPUS', kind: 'learning', shape: 'slab', desc: 'Curated datasets rolled up from run logs.' },
    { id: 'FINETUNE', name: 'FINE-TUNE WORKER', kind: 'learning', shape: 'slab', desc: 'Unsloth LoRA pipeline.' },
    { id: 'EVAL-HARNESS', name: 'EVAL HARNESS', kind: 'learning', shape: 'slab', desc: 'Rubric scoring across live benches.' },
    { id: 'RUN-LOGS', name: 'RUN LOGS', kind: 'logs', shape: 'diamond', desc: 'Append-only event journal.' },
    { id: 'PROMPT-HIST', name: 'PROMPT HISTORY', kind: 'logs', shape: 'diamond', desc: 'Every inference, replayable.' },
    { id: 'PII-DET', name: 'PII DETECTOR', kind: 'safety', shape: 'shield', desc: 'Presidio + custom patterns.' },
    { id: 'GUARDRAIL', name: 'GUARDRAILS', kind: 'safety', shape: 'shield', desc: 'Policy envelope.' },
    { id: 'MCP-HUB', name: 'MCP TOOL HUB', kind: 'tools', shape: 'slab', desc: 'Registered MCP servers.' },
    { id: 'MODEL-ROUTER', name: 'MODEL ROUTER', kind: 'tools', shape: 'slab', desc: 'Fallback chain + cost-aware routing.' },
    { id: 'TELEMETRY', name: 'TELEMETRY BUS', kind: 'infra', shape: 'slab', desc: 'OpenTelemetry · traces + metrics.' },
  ];
  return table.map((t) => {
    const rng = mulberry32(seedFromString(t.id));
    const out: HudActor = {
      ...t,
      status: rng() < 0.85 ? 'active' : 'idle',
      throughput: Math.floor(80 + rng() * 1600),
      errorRate: +(rng() * 0.02).toFixed(3),
      uptime: Math.floor(86400 + rng() * 86400 * 14),
      version: `v${Math.floor(rng() * 3) + 1}.${Math.floor(rng() * 14)}`,
      projects: allProjectIds,
      nodeClass: 'actor',
    };
    if (t.kind === 'memory') {
      out.rows =
        (t.id === 'VEC-STORE' ? overrides.vectorRows : overrides.graphRows) ??
        Math.floor(200000 + rng() * 3_000_000);
      out.qps = Math.floor(40 + rng() * 400);
    }
    if (t.kind === 'logs') {
      out.eventsPerSec = Math.floor(80 + rng() * 900);
      out.retentionDays = 7;
    }
    if (t.kind === 'learning') {
      out.activeJobs = Math.floor(rng() * 6);
      out.queued = Math.floor(rng() * 12);
    }
    if (t.kind === 'safety') {
      out.blockedToday = Math.floor(rng() * 48);
      out.rules = Math.floor(20 + rng() * 40);
    }
    if (t.id === 'MCP-HUB' && overrides.mcpCount != null) {
      out.desc = `${overrides.mcpCount} MCP servers registered.`;
    }
    return out;
  });
}

/* ─── Build project list with the synthetic "ALL PROJECTS" head row ─── */

const PROJECT_PALETTE = [
  { color: 'var(--amber)', codeFallback: 'CRE' },
  { color: 'var(--cyan)', codeFallback: 'HEL' },
  { color: 'var(--green)', codeFallback: 'FRG' },
  { color: 'var(--red)', codeFallback: 'AEG' },
  { color: 'var(--violet)', codeFallback: 'IRS' },
];

const DEMO_PROJECTS_FALLBACK: HudProject[] = [
  { id: 'prj-core', name: 'SAGEWAI CORE', color: 'var(--amber)', code: 'SGW', desc: 'Internal platform ops & orchestration.' },
  { id: 'prj-helios', name: 'HELIOS RESEARCH', color: 'var(--cyan)', code: 'HEL', desc: 'Long-form research & synthesis tenant.' },
  { id: 'prj-forge', name: 'FORGE DEVOPS', color: 'var(--green)', code: 'FRG', desc: 'Code generation & CI/CD pipelines.' },
  { id: 'prj-aegis', name: 'AEGIS COMPLIANCE', color: 'var(--red)', code: 'AEG', desc: 'Policy, audit, guardrail coverage.' },
  { id: 'prj-iris', name: 'IRIS MULTIMODAL', color: 'var(--violet)', code: 'IRS', desc: 'Image / audio / doc ingestion.' },
];

/**
 * Build the project bar. Always starts with the ALL PROJECTS aggregator tab.
 *
 * @param real  The real tenants returned by /api/v1/projects.
 * @param demoFallback  When true, and `real` is empty, pad with the demo tenants
 *   from the handoff mock so the bar isn't bare. Should only be true in demo mode
 *   (backend unreachable). In live mode with zero real projects, we show ONLY the
 *   ALL PROJECTS tab — the truthful empty state.
 */
export function buildProjects(
  real: Array<{ slug: string; name: string }>,
  demoFallback = false,
): HudProject[] {
  const head: HudProject = {
    id: 'prj-all',
    name: 'ALL PROJECTS',
    color: 'var(--cyan)',
    code: '*',
    desc: 'Federated view across every tenant.',
  };
  if (!real.length && demoFallback) {
    return [head, ...DEMO_PROJECTS_FALLBACK];
  }
  return [
    head,
    ...real.map((p, i) => {
      const palette = PROJECT_PALETTE[i % PROJECT_PALETTE.length];
      return {
        id: `prj-${p.slug}`,
        name: p.name.toUpperCase(),
        color: palette.color,
        code: p.slug.slice(0, 3).toUpperCase() || palette.codeFallback,
        desc: `${p.name} tenant.`,
      };
    }),
  ];
}

/* ─── Derive links from role heuristics + workflow paths ─── */

function agentByRole(agents: HudAgent[], role: HudRole): HudAgent | undefined {
  return agents.find((a) => a.role === role);
}

function allByRole(agents: HudAgent[], role: HudRole): HudAgent[] {
  return agents.filter((a) => a.role === role);
}

export function deriveLinks(
  agents: HudAgent[],
  actors: HudActor[],
  workflows: HudWorkflow[],
): HudLink[] {
  const links = new Map<string, HudLink>();
  const add = (source: string, target: string, kind: string) => {
    if (source === target) return;
    const key = `${source}→${target}`;
    if (links.has(key)) return;
    const rng = mulberry32(seedFromString(key));
    links.set(key, { source, target, kind, volume: 0.2 + rng() * 0.8 });
  };

  const orch = agentByRole(agents, 'orchestrator');
  const planners = allByRole(agents, 'planner');
  const researchers = allByRole(agents, 'researcher');
  const coders = allByRole(agents, 'coder');
  const reviewers = allByRole(agents, 'reviewer');
  const tools = allByRole(agents, 'tool');
  const memories = allByRole(agents, 'memory');
  const evaluators = allByRole(agents, 'evaluator');
  const sentinels = allByRole(agents, 'sentinel');

  // Orchestrator dispatches to planners, sentinels, tools
  planners.forEach((p) => orch && add(orch.id, p.id, 'dispatch'));
  sentinels.forEach((s) => orch && add(orch.id, s.id, 'policy'));
  tools.forEach((t) => orch && add(orch.id, t.id, 'dispatch'));

  // Planners → researchers / coders
  planners.forEach((p) => {
    researchers.forEach((r) => add(p.id, r.id, 'task'));
    coders.forEach((c) => add(p.id, c.id, 'task'));
  });

  // Researchers → memory, tool brokers
  researchers.forEach((r) => {
    memories.forEach((m) => add(r.id, m.id, 'recall'));
    tools.forEach((t) => add(r.id, t.id, 'tool'));
  });

  // Coders → reviewers
  coders.forEach((c) => reviewers.forEach((rv) => add(c.id, rv.id, 'review')));

  // Reviewers → orchestrator (feedback)
  reviewers.forEach((rv) => orch && add(rv.id, orch.id, 'report'));

  // Evaluators → orchestrator
  evaluators.forEach((ev) => orch && add(ev.id, orch.id, 'score'));
  coders.forEach((c) => evaluators.forEach((ev) => add(c.id, ev.id, 'score')));

  // Sentinels → guardrail / pii actors
  sentinels.forEach((s) => {
    add(s.id, 'PII-DET', 'scan');
    add(s.id, 'GUARDRAIL', 'enforce');
  });

  // Memory agents → vector store / graph db
  memories.forEach((m) => {
    add(m.id, 'VEC-STORE', 'store');
    add(m.id, 'GRAPH-DB', 'store');
  });

  // Tool brokers → MCP hub + model router
  tools.forEach((t) => add(t.id, 'MCP-HUB', 'tool'));
  if (orch) {
    add(orch.id, 'MODEL-ROUTER', 'route');
    add(orch.id, 'RUN-LOGS', 'log');
    add(orch.id, 'PROMPT-HIST', 'log');
  }
  coders.forEach((c) => add(c.id, 'MODEL-ROUTER', 'route'));

  // Evaluator → eval harness
  evaluators.forEach((ev) => add(ev.id, 'EVAL-HARNESS', 'score'));

  // Telemetry → logs
  add('TELEMETRY', 'RUN-LOGS', 'stream');
  add('TELEMETRY', 'PROMPT-HIST', 'stream');
  add('RUN-LOGS', 'CORPUS', 'train');
  add('CORPUS', 'FINETUNE', 'train');
  add('FINETUNE', 'MODEL-ROUTER', 'deploy');

  // Workflow paths (these are canonical)
  workflows.forEach((wf) => {
    for (let i = 0; i < wf.path.length - 1; i++) {
      add(wf.path[i], wf.path[i + 1], 'workflow');
    }
  });

  // Drop links that reference nodes that don't exist in the current graph
  const knownIds = new Set([...agents.map((a) => a.id), ...actors.map((a) => a.id)]);
  return [...links.values()].filter((l) => knownIds.has(l.source) && knownIds.has(l.target));
}

/* ─── Fallback demo roster (used when the real backend has zero agents,
       so a fresh install doesn't land on an empty HUD) ─── */

export const DEMO_AGENT_SEEDS: PartialAgentInput[] = [
  { id: 'SAGE-01', name: 'SAGEWAI', projects: ['prj-core', 'prj-helios', 'prj-forge', 'prj-aegis', 'prj-iris'] },
  { id: 'HELIOS-02', name: 'HELIOS', projects: ['prj-core', 'prj-helios', 'prj-forge'] },
  { id: 'ORACLE-03', name: 'ORACLE', projects: ['prj-helios', 'prj-iris'] },
  { id: 'SCRIBE-04', name: 'SCRIBE', projects: ['prj-helios'] },
  { id: 'FORGE-05', name: 'FORGE', projects: ['prj-forge', 'prj-core'] },
  { id: 'VULCAN-06', name: 'VULCAN', projects: ['prj-forge'] },
  { id: 'ARGUS-07', name: 'ARGUS', projects: ['prj-forge', 'prj-aegis'] },
  { id: 'NEMESIS-08', name: 'NEMESIS', projects: ['prj-forge', 'prj-aegis'] },
  { id: 'HERMES-09', name: 'HERMES', projects: ['prj-core', 'prj-iris', 'prj-helios'] },
  { id: 'MNEMOS-10', name: 'MNEMOSYNE', projects: ['prj-core', 'prj-helios', 'prj-forge', 'prj-iris'] },
  { id: 'THEMIS-11', name: 'THEMIS', projects: ['prj-forge', 'prj-helios'] },
  { id: 'AEGIS-12', name: 'AEGIS', projects: ['prj-aegis', 'prj-core', 'prj-forge', 'prj-helios', 'prj-iris'] },
  { id: 'IRIS-13', name: 'IRIS', projects: ['prj-iris', 'prj-helios'] },
  { id: 'KAIROS-14', name: 'KAIROS', projects: ['prj-core', 'prj-forge'] },
];

export const DEMO_WORKFLOWS: HudWorkflow[] = [
  { id: 'WF-A91', name: 'RESEARCH → DRAFT → REVIEW', path: ['SAGE-01', 'HELIOS-02', 'ORACLE-03', 'SCRIBE-04', 'ARGUS-07', 'NEMESIS-08', 'SAGE-01'] },
  { id: 'WF-B47', name: 'CODE → BUILD → EVAL', path: ['SAGE-01', 'HELIOS-02', 'FORGE-05', 'VULCAN-06', 'THEMIS-11', 'SAGE-01'] },
  { id: 'WF-C22', name: 'INGEST → INDEX → RECALL', path: ['IRIS-13', 'MNEMOS-10', 'VEC-STORE', 'ORACLE-03'] },
  { id: 'WF-D08', name: 'POLICY SWEEP', path: ['SAGE-01', 'AEGIS-12', 'PII-DET', 'GUARDRAIL', 'HERMES-09'] },
  { id: 'WF-E15', name: 'SCHEDULED REGRESSIONS', path: ['KAIROS-14', 'FORGE-05', 'ARGUS-07', 'THEMIS-11', 'EVAL-HARNESS'] },
  { id: 'WF-F33', name: 'LOGS → CORPUS → FINE-TUNE', path: ['RUN-LOGS', 'CORPUS', 'FINETUNE', 'MODEL-ROUTER'] },
];

/* ─── Utility: format uptime for display ─── */

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${String(d).padStart(2, '0')}D:${String(h).padStart(2, '0')}H:${String(m).padStart(2, '0')}M`;
}
