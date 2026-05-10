'use client';

import { useEffect, useReducer, useRef, useState } from 'react';
import { AutopilotStatusBadge } from '@/components/autopilot-status-badge';
import type { MissionRunEvent } from '@/utils/types';

// ── Public API ─────────────────────────────────────────────────────────────

export type { MissionRunEvent };

export function AutopilotMissionLiveTrace({
  missionId,
  initialEvents = [],
  initialOutput = null,
  initialStatus = 'running',
}: {
  missionId: string;
  initialEvents?: MissionRunEvent[];
  initialOutput?: unknown;
  initialStatus?: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
}) {
  const [state, dispatch] = useReducer(traceReducer, undefined, () =>
    buildInitialState(initialEvents, initialStatus),
  );
  // Dedup set — populated from initialEvents so the SSE replay doesn't double-render.
  const seenIds = useRef<Set<string>>(
    new Set(initialEvents.map((e) => e.event_id)),
  );

  useEffect(() => {
    if (typeof window === 'undefined') return;
    // Terminal states: don't open SSE at all.
    if (
      initialStatus === 'completed' ||
      initialStatus === 'failed' ||
      initialStatus === 'cancelled'
    ) {
      return;
    }

    const es = new EventSource(
      `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/events`,
    );
    let closed = false;

    function handleEvent(raw: MessageEvent) {
      if (closed) return;
      let event: MissionRunEvent;
      try {
        event = JSON.parse(raw.data) as MissionRunEvent;
      } catch {
        return;
      }
      if (seenIds.current.has(event.event_id)) return;
      seenIds.current.add(event.event_id);
      dispatch({ type: 'EVENT', event });
      if (event.kind === 'mission.finished') {
        closed = true;
        es.close();
      }
    }

    // The backend uses `event: <kind>` field, so listen generically via onmessage
    // (all SSE events without an explicit type field arrive as 'message').
    // We also attach named listeners for each plan-H kind.
    const KINDS = [
      'mission.started',
      'agent.started',
      'agent.tool_call',
      'agent.tool_result',
      'agent.tool_failed',
      'agent.llm_call',
      'agent.finished',
      'mission.finished',
    ] as const;

    es.onmessage = handleEvent;
    for (const kind of KINDS) {
      es.addEventListener(kind, handleEvent as EventListener);
    }

    es.onerror = () => {
      if (!closed) es.close();
    };

    return () => {
      closed = true;
      es.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missionId]);

  const nodeIds = Object.keys(state.byNode);

  return (
    <div className="flex flex-col gap-4" data-testid="mission-live-trace">
      {/* Header row */}
      <div className="flex items-center gap-3">
        <span data-testid="status-badge">
          <AutopilotStatusBadge status={state.status} />
        </span>
        <CostTicker usd={state.costUsd} />
      </div>

      {/* Per-node timelines — aria-live so screen readers announce new entries */}
      {nodeIds.length === 0 ? (
        <p className="text-sm text-text-secondary" role="status">Waiting for events…</p>
      ) : (
        <ol
          aria-live="polite"
          aria-atomic="false"
          aria-label="Mission live trace"
          className="flex flex-col gap-4 list-none p-0 m-0"
        >
          {nodeIds.map((nodeId) => (
            <AgentTimelineRow
              key={nodeId}
              nodeId={nodeId}
              events={state.byNode[nodeId]}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

// ── Internal sub-components ────────────────────────────────────────────────

function CostTicker({ usd }: { usd: number }) {
  const label = usd > 0 ? `~$${usd.toFixed(4)}` : '–';
  return (
    <span
      className="text-xs font-mono text-text-secondary tabular-nums"
      data-testid="cost-ticker"
    >
      {label}
    </span>
  );
}

function AgentTimelineRow({
  nodeId,
  events,
}: {
  nodeId: string;
  events: MissionRunEvent[];
}) {
  return (
    <li
      className="rounded-lg border border-border bg-bg-surface p-4 flex flex-col gap-3"
      data-testid="agent-row"
    >
      <h3 className="text-sm font-semibold text-text-primary m-0">{nodeId}</h3>
      <ol className="flex flex-col gap-1.5 list-none p-0 m-0">
        {events.map((event) => (
          <EventLine key={event.event_id} event={event} />
        ))}
      </ol>
    </li>
  );
}

function EventLine({ event }: { event: MissionRunEvent }) {
  if (event.kind === 'agent.tool_call' || event.kind === 'agent.tool_result') {
    return <ToolCallDetail event={event} />;
  }
  if (event.kind === 'agent.tool_failed') {
    return (
      <li className="flex flex-col gap-1">
        <EventPill event={event} />
        {event.error && (
          <p className="text-xs text-error font-mono break-all ml-4 m-0">
            {event.error}
          </p>
        )}
      </li>
    );
  }
  return (
    <li>
      <EventPill event={event} />
    </li>
  );
}

function EventPill({ event }: { event: MissionRunEvent }) {
  const time = new Date(event.ts).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  return (
    <span className="flex items-center gap-2 text-xs text-text-secondary">
      <span className="font-mono text-text-muted tabular-nums">{time}</span>
      <KindBadge kind={event.kind} />
      {event.tool && (
        <span className="font-mono text-text-primary">{event.tool}</span>
      )}
      {event.model && (
        <span className="text-text-muted">{event.model}</span>
      )}
      {event.cost_usd != null && event.cost_usd > 0 && (
        <span className="tabular-nums text-text-muted">
          ${event.cost_usd.toFixed(4)}
        </span>
      )}
      {event.latency_ms != null && (
        <span className="tabular-nums text-text-muted">
          {event.latency_ms}ms
        </span>
      )}
    </span>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const colorMap: Record<string, string> = {
    'mission.started': 'text-primary',
    'mission.finished': 'text-success',
    'agent.started': 'text-secondary',
    'agent.finished': 'text-secondary',
    'agent.llm_call': 'text-warning',
    'agent.tool_call': 'text-text-primary',
    'agent.tool_result': 'text-text-primary',
    'agent.tool_failed': 'text-error',
  };
  const color = colorMap[kind] ?? 'text-text-muted';
  return (
    <span className={`font-mono font-medium ${color}`}>{kind}</span>
  );
}

function ToolCallDetail({ event }: { event: MissionRunEvent }) {
  const [open, setOpen] = useState(false);

  const hasDetail =
    event.output != null || event.output_preview != null || event.error != null;

  return (
    <li className="flex flex-col gap-1">
      <button
        type="button"
        className="flex items-center gap-2 text-left w-full hover:bg-bg-subtle rounded px-1 -mx-1 transition-colors"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="text-text-muted text-xs select-none">
          {open ? '▾' : '▸'}
        </span>
        <EventPill event={event} />
      </button>
      {open && hasDetail && (
        <div
          className="ml-4 rounded border border-border bg-bg-subtle p-2"
          data-testid="tool-call-detail"
        >
          {event.error && (
            <p className="text-xs text-error font-mono break-all m-0">
              Error: {event.error}
            </p>
          )}
          {(event.output != null || event.output_preview != null) && (
            <pre className="text-xs font-mono text-text-primary overflow-auto max-h-64 m-0 whitespace-pre-wrap break-all">
              {typeof event.output === 'string'
                ? event.output
                : event.output != null
                  ? JSON.stringify(event.output, null, 2)
                  : event.output_preview}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

// ── Reducer ────────────────────────────────────────────────────────────────

type TraceStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

interface TraceState {
  events: MissionRunEvent[];
  byNode: Record<string, MissionRunEvent[]>;
  costUsd: number;
  status: TraceStatus;
}

type TraceAction = { type: 'EVENT'; event: MissionRunEvent };

function buildInitialState(
  initialEvents: MissionRunEvent[],
  initialStatus: TraceStatus,
): TraceState {
  const byNode: Record<string, MissionRunEvent[]> = {};
  let costUsd = 0;

  for (const e of initialEvents) {
    const key = e.node_id ?? e.agent_id ?? 'unknown';
    if (!byNode[key]) byNode[key] = [];
    byNode[key].push(e);
    if (e.kind === 'agent.llm_call' && e.cost_usd != null) {
      costUsd += e.cost_usd;
    }
    if (e.kind === 'mission.finished' && e.total_cost_usd != null) {
      costUsd = e.total_cost_usd;
    }
  }

  return { events: [...initialEvents], byNode, costUsd, status: initialStatus };
}

function traceReducer(state: TraceState, action: TraceAction): TraceState {
  if (action.type !== 'EVENT') return state;

  const event = action.event;
  const key = event.node_id ?? event.agent_id ?? 'unknown';

  const newEvents = [...state.events, event];
  const newByNode: Record<string, MissionRunEvent[]> = { ...state.byNode };
  newByNode[key] = [...(newByNode[key] ?? []), event];

  let costUsd = state.costUsd;
  if (event.kind === 'agent.llm_call' && event.cost_usd != null) {
    costUsd += event.cost_usd;
  }
  if (event.kind === 'mission.finished' && event.total_cost_usd != null) {
    costUsd = event.total_cost_usd;
  }

  let status: TraceStatus = state.status;
  if (event.kind === 'mission.finished') {
    const s = event.status;
    if (s === 'completed' || s === 'failed' || s === 'cancelled') {
      status = s;
    } else {
      status = 'completed';
    }
  }

  return { events: newEvents, byNode: newByNode, costUsd, status };
}
