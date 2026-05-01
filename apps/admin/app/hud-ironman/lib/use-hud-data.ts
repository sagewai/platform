/**
 * useHudData — fetches real data from the admin backend and synthesizes the
 * fields the HUD needs but the API doesn't yet expose.
 *
 * Live polling strategy:
 *   - projects / agent list / workflow list: fetched once on mount
 *   - per-agent detail (tools, prompt, temperature): fetched in parallel
 *     on mount so the inspector is instant when any agent is clicked
 *   - cost + runs: polled every 5s for KPIs (active count, runs/24h, tokens/24h, cost, errors/1h)
 *   - latest runs: polled every ~1s for the ticker tail
 *
 * The hook is resilient: if any single endpoint fails it keeps the last-known
 * state and continues. If the list of agents is empty on first load (fresh
 * install) we populate from DEMO_AGENT_SEEDS so the monitor isn't blank.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { adminApi } from '@/utils/api';
import { authFetch } from '@/utils/auth';
import type {
  AgentSummary,
  AgentDetail,
  Project,
  RunSummary,
  SavedWorkflow,
} from '@/utils/types';

const ADMIN_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL ?? 'http://localhost:8000/admin';
const ANALYTICS_BASE = ADMIN_BASE.replace(/\/admin$/, '');

interface PlaygroundAgentDetail {
  name: string;
  model: string;
  system_prompt: string;
  temperature: number;
  top_p?: number | null;
  max_tokens?: number | null;
  tools: string[];
  capabilities: string[];
  project_id: string | null;
}

async function fetchAgentDetail(summary: AgentSummary): Promise<AgentDetail | PlaygroundAgentDetail | null> {
  try {
    if (summary.source === 'playground') {
      const res = await authFetch(
        `${ANALYTICS_BASE}/playground/agents/${encodeURIComponent(summary.name)}`,
      );
      if (!res.ok) return null;
      return (await res.json()) as PlaygroundAgentDetail;
    }
    return await adminApi.getAgent(summary.name);
  } catch {
    return null;
  }
}
import {
  ALERT_POOL,
  DEMO_AGENT_SEEDS,
  DEMO_WORKFLOWS,
  buildActors,
  buildProjects,
  deriveLinks,
  finalizeAgent,
  synthesizeAgent,
  type HudActor,
  type HudAgent,
  type HudLink,
  type HudProject,
  type HudTickerEvent,
  type HudWorkflow,
} from './hud-data';

export interface HudKpis {
  activeAgents: number;
  runs24h: number;
  tokens24h: number;
  spendToday: number;
  errors1h: number;
}

export type HudMode = 'live' | 'demo' | 'loading';

export interface HudLive {
  agents: HudAgent[];
  actors: HudActor[];
  projects: HudProject[];
  workflows: HudWorkflow[];
  links: HudLink[];
  kpis: HudKpis;
  ticker: HudTickerEvent[];
  loading: boolean;
  mode: HudMode;
}

// Extract a path of agent IDs from a SavedWorkflow's YAML content by scanning
// for known agent names in order of appearance. Keeps us off a YAML-parse
// dependency tree and is robust to varying schema.
function extractWorkflowPath(yaml: string, agentIds: string[]): string[] {
  const positions: Array<{ id: string; pos: number }> = [];
  for (const id of agentIds) {
    const re = new RegExp(`\\b${id.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')}\\b`, 'g');
    let m;
    while ((m = re.exec(yaml)) !== null) {
      positions.push({ id, pos: m.index });
    }
  }
  positions.sort((a, b) => a.pos - b.pos);
  // De-dupe consecutive repeats
  const out: string[] = [];
  for (const p of positions) {
    if (out[out.length - 1] !== p.id) out.push(p.id);
  }
  return out;
}

export function useHudData(): HudLive {
  const [agents, setAgents] = useState<HudAgent[]>([]);
  const [actors, setActors] = useState<HudActor[]>([]);
  const [projects, setProjects] = useState<HudProject[]>([]);
  const [workflows, setWorkflows] = useState<HudWorkflow[]>([]);
  const [ticker, setTicker] = useState<HudTickerEvent[]>([]);
  const [kpis, setKpis] = useState<HudKpis>({
    activeAgents: 0,
    runs24h: 0,
    tokens24h: 0,
    spendToday: 0,
    errors1h: 0,
  });
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<HudMode>('loading');

  const seenRunIdsRef = useRef<Set<string>>(new Set());

  // Initial load — projects, agents (list + details in parallel), workflows, actor stats.
  //
  // Mode selection:
  //   - 'live' when the critical calls (projects + agents + workflows) all return a
  //     successful response, even if the arrays are empty. An empty live mode shows
  //     empty panels + a calm graph; it faithfully reflects a fresh install.
  //   - 'demo' only when every one of those three calls REJECTS (backend truly
  //     unreachable, auth refused, etc). In that case we show the 14-agent
  //     synthetic fleet so a disconnected monitor still looks alive.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [projResult, agentListResult, wfResult, vectorResult, graphResult, mcpResult] =
        await Promise.allSettled([
          adminApi.listProjects(),
          adminApi.listAgents(),
          adminApi.listSavedWorkflows({ limit: 50 }),
          adminApi.getVectorStats(),
          adminApi.getGraphStats(),
          adminApi.listMcpServers(),
        ]);

      if (cancelled) return;

      const anyCoreSucceeded =
        projResult.status === 'fulfilled' ||
        agentListResult.status === 'fulfilled' ||
        wfResult.status === 'fulfilled';
      const liveMode = anyCoreSucceeded;

      if (!liveMode) {
        // Everything rejected → demo-mode fallback (backend unreachable)
        const demoProjects = buildProjects([], true);
        const projectIds = demoProjects.map((p) => p.id);
        const demoActors = buildActors(projectIds.filter((id) => id !== 'prj-all'));
        const demoAgents = DEMO_AGENT_SEEDS.map((s) =>
          finalizeAgent(synthesizeAgent({ ...s, projects: s.projects ?? ['prj-core'] })),
        );
        setProjects(demoProjects);
        setActors(demoActors);
        setAgents(demoAgents);
        setWorkflows(DEMO_WORKFLOWS);
        setMode('demo');
        setLoading(false);
        return;
      }

      // Live mode — use real data, even if empty.
      const realProjects: Project[] =
        projResult.status === 'fulfilled' ? projResult.value : [];
      const builtProjects = buildProjects(realProjects);
      const realProjectIds = builtProjects.filter((p) => p.id !== 'prj-all').map((p) => p.id);
      setProjects(builtProjects);

      const vectorRows =
        vectorResult.status === 'fulfilled' && typeof vectorResult.value?.documents === 'number'
          ? vectorResult.value.documents
          : undefined;
      const graphRows =
        graphResult.status === 'fulfilled' && typeof graphResult.value?.entities === 'number'
          ? graphResult.value.entities
          : undefined;
      const mcpCount =
        mcpResult.status === 'fulfilled' && Array.isArray(mcpResult.value)
          ? mcpResult.value.length
          : undefined;

      const agentList: AgentSummary[] =
        agentListResult.status === 'fulfilled' && Array.isArray(agentListResult.value)
          ? agentListResult.value
          : [];

      // Actors represent infrastructure (vector store, MCP hub, guardrails, etc.).
      // They only make sense once agents exist to orbit them. In live-empty mode
      // we skip them entirely so the counters match what's drawn.
      const builtActors =
        agentList.length === 0
          ? []
          : buildActors(realProjectIds, { vectorRows, graphRows, mcpCount });
      setActors(builtActors);

      // Use source-aware detail fetcher: playground agents live at
      // /playground/agents/{name}, registered agents at /admin/agents/{name}.
      const detailResults = await Promise.all(agentList.map(fetchAgentDetail));
      if (cancelled) return;

      const defaultProjectIds = realProjectIds.length ? realProjectIds : [];
      const liveAgents: HudAgent[] = agentList.map((summary, i) => {
        const detail = detailResults[i];
        const totalRuns = detail && 'total_runs' in detail ? detail.total_runs : undefined;
        const capabilities =
          detail && 'capabilities' in detail && detail.capabilities?.length
            ? detail.capabilities
            : summary.capabilities;
        return finalizeAgent(
          synthesizeAgent({
            id: summary.name,
            name: summary.name.toUpperCase(),
            model: detail?.model || summary.model,
            systemPrompt: detail?.system_prompt || undefined,
            temperature: detail?.temperature ?? undefined,
            topP: detail?.top_p ?? undefined,
            maxTokens: detail?.max_tokens ?? undefined,
            tools: detail?.tools,
            capabilities,
            tags: summary.tags,
            status: summary.status,
            totalRuns,
            projects: defaultProjectIds,
            desc: capabilities[0] ? `Capability: ${capabilities[0]}` : undefined,
          }),
        );
      });
      setAgents(liveAgents);

      const savedWorkflows: SavedWorkflow[] =
        wfResult.status === 'fulfilled' ? wfResult.value.items : [];
      const agentIds = liveAgents.map((a) => a.id);
      const actorIds = builtActors.map((a) => a.id);
      const knownIds = [...agentIds, ...actorIds];

      const liveWorkflows: HudWorkflow[] = savedWorkflows
        .map((wf, i) => {
          const path = extractWorkflowPath(wf.yaml_content || '', knownIds);
          return {
            id: `WF-${wf.id.slice(0, 3).toUpperCase()}${String(i + 1).padStart(2, '0')}`,
            name: wf.name.toUpperCase(),
            path,
          };
        })
        .filter((wf) => wf.path.length >= 2);
      setWorkflows(liveWorkflows);

      setMode('live');
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Slow poll: KPIs (cost + runs count) every 5s
  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    const tick = async () => {
      const [costResult, runsResult] = await Promise.allSettled([
        adminApi.getCosts(),
        adminApi.listRuns({ limit: 100 }),
      ]);
      if (cancelled) return;
      const cost = costResult.status === 'fulfilled' ? costResult.value : null;
      const runsPage = runsResult.status === 'fulfilled' ? runsResult.value : null;
      const items: RunSummary[] = runsPage?.items ?? [];
      const now = Date.now() / 1000;
      const oneHourAgo = now - 3600;

      const runs24h = items.length; // API pagination == approximate 24h window
      const tokens24h = items.reduce((s, r) => s + (r.total_tokens || 0), 0);
      const errors1h = items.filter(
        (r) => r.status === 'failed' && (r.started_at ?? 0) >= oneHourAgo,
      ).length;
      const activeAgents = items
        .filter((r) => r.status === 'running' || r.status === 'pending')
        .reduce((set, r) => set.add(r.agent_name), new Set<string>()).size;

      setKpis((prev) => ({
        activeAgents: activeAgents || prev.activeAgents, // don't flicker to 0 when idle
        runs24h: runs24h || prev.runs24h,
        tokens24h: tokens24h || prev.tokens24h,
        spendToday: cost?.total_cost_usd ?? prev.spendToday,
        errors1h,
      }));
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [loading]);

  // Fast poll: ticker tail every 1s. We diff against seenRunIds to only emit
  // tick lines for runs we haven't seen. Fallback: synthesized nominal chatter
  // when the backend is quiet so the ticker always feels alive.
  useEffect(() => {
    if (loading || agents.length === 0) return;
    let cancelled = false;
    const tick = async () => {
      const result = await adminApi.listRuns({ limit: 10 }).catch(() => null);
      if (cancelled) return;
      const items: RunSummary[] = result?.items ?? [];

      const newEvents: HudTickerEvent[] = [];
      for (const r of items) {
        if (seenRunIdsRef.current.has(r.run_id)) continue;
        seenRunIdsRef.current.add(r.run_id);
        const fromAgent = agents.find((a) => a.id === r.agent_name) ?? agents[0];
        const peers = agents.filter((a) => a.id !== fromAgent.id);
        const toAgent = peers[Math.floor(Math.random() * peers.length)] ?? fromAgent;
        let tag: HudTickerEvent['tag'] = 'ACK';
        let msg = `run ${r.run_id.slice(0, 8)} ${r.status}.`;
        if (r.status === 'failed') {
          tag = 'ERR';
          msg = `run ${r.run_id.slice(0, 8)} failed.`;
        } else if (r.status === 'running') {
          tag = 'TX';
          msg = `streaming run ${r.run_id.slice(0, 8)}.`;
        } else if (r.status === 'completed') {
          tag = 'NOM';
          msg = `run ${r.run_id.slice(0, 8)} ok · ${r.total_tokens} tok.`;
        }
        newEvents.push({ tag, from: fromAgent.name, to: toAgent.name, msg, ts: Date.now() });
      }

      // If no real events this tick, inject synthetic chatter — but ONLY in demo
      // mode. In live mode a quiet ticker is the truth: nothing is happening.
      if (mode === 'demo' && newEvents.length === 0 && Math.random() < 0.7) {
        const from = agents[Math.floor(Math.random() * agents.length)];
        const peers = agents.filter((a) => a.id !== from.id);
        const to = peers[Math.floor(Math.random() * peers.length)] ?? from;
        const [tag, msg] = ALERT_POOL[Math.floor(Math.random() * ALERT_POOL.length)];
        newEvents.push({ tag, from: from.name, to: to.name, msg, ts: Date.now() });
      }

      if (newEvents.length) {
        setTicker((prev) => [...newEvents.reverse(), ...prev].slice(0, 12));
      }
    };
    const id = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [loading, agents, mode]);

  const links = useMemo(
    () => (agents.length && actors.length ? deriveLinks(agents, actors, workflows) : []),
    [agents, actors, workflows],
  );

  return useMemo(
    () => ({ agents, actors, projects, workflows, links, kpis, ticker, loading, mode }),
    [agents, actors, projects, workflows, links, kpis, ticker, loading, mode],
  );
}

/* ─── Helper exposed separately so the inspector can lazy-refresh an agent
       detail when selected (e.g. to pull the latest system prompt). ─── */

export function useAgentDetail(agentName: string | null): AgentDetail | null {
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const load = useCallback(async () => {
    if (!agentName) {
      setDetail(null);
      return;
    }
    try {
      const d = await adminApi.getAgent(agentName);
      setDetail(d);
    } catch {
      setDetail(null);
    }
  }, [agentName]);
  useEffect(() => {
    load();
  }, [load]);
  return detail;
}
