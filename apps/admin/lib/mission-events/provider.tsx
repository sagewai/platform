'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { MissionRunEvent } from '@/utils/types';

type Listener = (event: MissionRunEvent) => void;
type Filter = (event: MissionRunEvent) => boolean;

interface MissionEventBus {
  subscribe(listener: Listener): () => void;
  history(): readonly MissionRunEvent[];
  last(filter?: Filter): MissionRunEvent | undefined;
}

const MissionEventCtx = createContext<MissionEventBus | null>(null);

export type ReplayScheduler = { flush: () => void } | null;

export interface MissionEventProviderProps {
  missionId: string;
  /** Pre-existing events (e.g. from a /trace replay payload) seeded into the bus on mount. */
  initialEvents?: readonly MissionRunEvent[];
  /** Set false when the mission is in a terminal state — provider skips opening SSE. */
  liveStream?: boolean;
  /** Replay events — when non-empty, schedules them at `replaySpeed` × real time. */
  replayEvents?: readonly MissionRunEvent[];
  /** Replay speed multiplier (default 4). */
  replaySpeed?: number;
  /** Optional callback that receives the replay scheduler (or null on teardown). */
  onReplayScheduler?: (scheduler: ReplayScheduler) => void;
  children: React.ReactNode;
}

/**
 * Single SSE connection per mission, fan-out via useMissionEvent(filter).
 *
 * Sources:
 *   - `liveStream` (default true): opens EventSource against
 *     /api/v1/autopilot/missions/{missionId}/events; consumes the same kinds
 *     as Plan H (mission.started, agent.started, agent.llm_call, agent.tool_call,
 *     agent.tool_result, agent.tool_failed, agent.finished, mission.finished).
 *   - `initialEvents`: pre-buffered history from /trace, replayed without delay.
 *   - `replay`: for terminal missions — schedules events at speed× real time.
 *
 * Events are deduped by `event_id`.
 */
export function MissionEventProvider({
  missionId,
  initialEvents,
  liveStream = true,
  replayEvents,
  replaySpeed = 4,
  onReplayScheduler,
  children,
}: MissionEventProviderProps) {
  const listenersRef = useRef<Set<Listener>>(new Set());
  const bufferRef = useRef<MissionRunEvent[]>([]);
  const seenRef = useRef<Set<string>>(new Set());
  const initialEventsRef = useRef(initialEvents);
  const replayEventsRef = useRef(replayEvents);
  const onReplaySchedulerRef = useRef(onReplayScheduler);
  onReplaySchedulerRef.current = onReplayScheduler;
  replayEventsRef.current = replayEvents;
  const [, force] = useState(0);

  // Seed initial events synchronously on first render so first-paint shows them.
  // Reads from the captured ref so a parent re-render with a fresh array doesn't
  // re-seed.
  if (bufferRef.current.length === 0 && initialEventsRef.current && initialEventsRef.current.length > 0) {
    for (const ev of initialEventsRef.current) {
      if (ev.event_id && !seenRef.current.has(ev.event_id)) {
        seenRef.current.add(ev.event_id);
        bufferRef.current.push(ev);
      }
    }
  }

  const emit = (event: MissionRunEvent) => {
    if (event.event_id && seenRef.current.has(event.event_id)) return;
    if (event.event_id) seenRef.current.add(event.event_id);
    bufferRef.current = [...bufferRef.current, event];
    listenersRef.current.forEach((l) => l(event));
    force((n) => n + 1);
  };

  // Replay vs live decision is a primitive boolean for stability.
  const replayActive = !!replayEvents && replayEvents.length > 0;

  // Live SSE stream.
  useEffect(() => {
    if (!liveStream || replayActive) return;
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;

    const url = `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/events`;
    const es = new EventSource(url);
    let closed = false;

    const handle = (raw: MessageEvent) => {
      if (closed) return;
      try {
        const ev = JSON.parse(raw.data) as MissionRunEvent;
        emit(ev);
      } catch {
        // malformed — ignore
      }
    };

    es.onmessage = handle;
    const NAMED_KINDS = [
      'mission.started',
      'agent.started',
      'agent.tool_call',
      'agent.tool_result',
      'agent.tool_failed',
      'agent.llm_call',
      'agent.finished',
      'mission.finished',
    ] as const;
    for (const kind of NAMED_KINDS) {
      es.addEventListener(kind, handle as EventListener);
    }
    es.onerror = () => {
      // Browser auto-reconnects; surface to a separate ConnectionStatus if needed.
    };

    return () => {
      closed = true;
      es.close();
    };
  }, [missionId, liveStream, replayActive]);

  // Replay scheduler — runs once per [replayActive, replaySpeed, missionId];
  // events are read from a ref so a fresh-array reference each render does not
  // re-fire the effect.
  useEffect(() => {
    if (!replayActive) return;
    const events = replayEventsRef.current;
    if (!events || events.length === 0) return;

    let cancelled = false;
    const handles: number[] = [];
    const t0 = new Date(events[0].ts).getTime();

    const flush = () => {
      handles.forEach((h) => window.clearTimeout(h));
      handles.length = 0;
      for (const ev of events) {
        if (cancelled) break;
        emit(ev);
      }
    };

    for (const ev of events) {
      const delay = Math.max(0, (new Date(ev.ts).getTime() - t0) / replaySpeed);
      const h = window.setTimeout(() => {
        if (!cancelled) emit(ev);
      }, delay);
      handles.push(h);
    }

    onReplaySchedulerRef.current?.({ flush });

    return () => {
      cancelled = true;
      handles.forEach((h) => window.clearTimeout(h));
      onReplaySchedulerRef.current?.(null);
    };
  }, [missionId, replayActive, replaySpeed]);

  const bus = useMemo<MissionEventBus>(
    () => ({
      subscribe: (listener) => {
        listenersRef.current.add(listener);
        return () => {
          listenersRef.current.delete(listener);
        };
      },
      history: () => bufferRef.current,
      last: (filter) => {
        const buf = bufferRef.current;
        if (!filter) return buf.at(-1);
        for (let i = buf.length - 1; i >= 0; i--) {
          if (filter(buf[i])) return buf[i];
        }
        return undefined;
      },
    }),
    [],
  );

  return <MissionEventCtx.Provider value={bus}>{children}</MissionEventCtx.Provider>;
}

/**
 * Subscribes to the mission event bus. Returns the most-recent event matching
 * `filter` (or any event if no filter). Re-renders only when a new matching
 * event arrives (useSyncExternalStore semantics).
 */
export function useMissionEvent(filter?: Filter): MissionRunEvent | undefined {
  const bus = useContext(MissionEventCtx);
  if (!bus) {
    throw new Error('useMissionEvent must be used inside <MissionEventProvider>');
  }
  // Stable getSnapshot — re-evaluates `last(filter)` on each subscriber notify.
  const filterRef = useRef(filter);
  filterRef.current = filter;
  return useSyncExternalStore(
    (cb) => bus.subscribe(() => cb()),
    () => bus.last(filterRef.current),
    () => undefined,
  );
}

/**
 * Subscribes to the full event history. Use when a component needs to
 * recompute over the whole list (e.g. CostBurnDownChart).
 */
export function useMissionEventHistory(): readonly MissionRunEvent[] {
  const bus = useContext(MissionEventCtx);
  if (!bus) {
    throw new Error('useMissionEventHistory must be used inside <MissionEventProvider>');
  }
  return useSyncExternalStore(
    (cb) => bus.subscribe(() => cb()),
    () => bus.history(),
    () => [],
  );
}
