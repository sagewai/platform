'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// ── types ─────────────────────────────────────────────────────────────

interface WorkerRef {
  worker_id: string;
  worker_name: string;
  probe_status: string | null;
}

interface StepAllocation {
  step_id: string;
  agent_id: string;
  role: string | null;
  tools: string[];
  matched_workers: WorkerRef[];
  claimed_worker_id: string | null;
}

interface PoolWorker {
  id: string;
  name: string;
  models_canonical: string[];
  pool: string;
  probe_status: string | null;
}

// ── Fleet pool header ─────────────────────────────────────────────────

function FleetPoolHeader({
  missionId,
  missionStatus,
}: {
  missionId: string;
  missionStatus?: string;
}) {
  const [workers, setWorkers] = useState<PoolWorker[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchWorkers = useCallback(async () => {
    try {
      const resp = await fetch(`/api/v1/autopilot/fleet/workers`, {
        credentials: 'include',
      });
      if (resp.ok) {
        const data = (await resp.json()) as PoolWorker[];
        if (Array.isArray(data)) setWorkers(data);
      }
    } catch {
      // silent — pool header is non-critical
    }
  }, []);

  useEffect(() => {
    void fetchWorkers();

    if (missionStatus === 'running') {
      const schedule = () => {
        timerRef.current = setTimeout(() => {
          void fetchWorkers().then(schedule);
        }, 5000);
      };
      schedule();
    }

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchWorkers, missionStatus]);

  const idle = workers.filter((w) => w.probe_status !== 'degraded').length;
  const busy = workers.filter((w) => w.probe_status === 'degraded').length;

  return (
    <div className="flex items-center gap-3 text-sm text-text-secondary mb-3">
      <span className="font-medium text-text-primary">
        {workers.length} worker{workers.length !== 1 ? 's' : ''}
      </span>
      {workers.length > 0 && (
        <>
          <span>·</span>
          <span className="text-success">{idle} idle</span>
          {busy > 0 && (
            <>
              <span>·</span>
              <span className="text-warning">{busy} busy</span>
            </>
          )}
        </>
      )}
      <a
        href="/fleet"
        className="ml-auto text-xs text-primary hover:underline"
      >
        View fleet →
      </a>
    </div>
  );
}

// ── Step row ──────────────────────────────────────────────────────────

function FleetStepRow({
  step,
  liveEvent,
}: {
  step: StepAllocation;
  liveEvent?: { kind: string; worker_id?: string; worker_name?: string; queue_position?: number };
}) {
  const isDispatched = liveEvent?.kind === 'agent.dispatched_to_worker';
  const isClaimed =
    liveEvent?.kind === 'agent.worker_claimed' || step.claimed_worker_id != null;
  const isNoWorker = liveEvent?.kind === 'agent.no_worker_available';

  return (
    <li className="flex flex-col gap-1 py-2 border-t border-border first:border-t-0">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-[family-name:var(--font-mono)] text-text-secondary text-xs w-24 truncate">
          {step.step_id}
        </span>
        {step.role && (
          <span className="text-text-secondary text-xs">{step.role}</span>
        )}
        {step.tools.length > 0 && (
          <div className="flex gap-1 ml-auto">
            {step.tools.slice(0, 3).map((t) => (
              <span
                key={t}
                className="rounded-full bg-bg-subtle border border-border text-text-secondary text-[10px] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
              >
                {t}
              </span>
            ))}
            {step.tools.length > 3 && (
              <span className="text-text-secondary text-[10px]">
                +{step.tools.length - 3}
              </span>
            )}
          </div>
        )}
      </div>

      <div className="text-xs">
        {isNoWorker ? (
          <span className="text-error">
            No compatible worker — check tool labels and model requirements
          </span>
        ) : isClaimed ? (
          <span className="text-success">
            Claimed by{' '}
            <span className="font-medium font-[family-name:var(--font-mono)]">
              {liveEvent?.worker_name ?? step.claimed_worker_id ?? 'worker'}
            </span>
          </span>
        ) : isDispatched ? (
          <span className="text-primary animate-pulse">
            Queued
            {liveEvent?.queue_position != null ? ` (#${liveEvent.queue_position})` : ''}
            …
          </span>
        ) : step.matched_workers.length === 0 ? (
          <span className="text-warning">No compatible workers in pool</span>
        ) : (
          <span className="text-text-secondary">
            {step.matched_workers.length} compatible worker
            {step.matched_workers.length !== 1 ? 's' : ''} available
          </span>
        )}
      </div>
    </li>
  );
}

// ── Main panel ────────────────────────────────────────────────────────

export function AutopilotFleetPanel({
  missionId,
  missionStatus,
  liveEventsByStep = {},
}: {
  missionId: string;
  missionStatus?: string;
  liveEventsByStep?: Record<string, { kind: string; worker_id?: string; worker_name?: string; queue_position?: number }>;
}) {
  const [allocation, setAllocation] = useState<StepAllocation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/fleet-allocation`, {
      credentials: 'include',
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((data: StepAllocation[]) => {
        if (!cancelled) setAllocation(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setAllocation([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  return (
    <section
      className="rounded-lg border border-border bg-bg-surface p-4"
      data-testid="fleet-panel"
    >
      <h3 className="text-sm font-semibold text-text-primary mb-2">Fleet allocation</h3>
      <FleetPoolHeader missionId={missionId} missionStatus={missionStatus} />

      {loading ? (
        <p className="text-sm text-text-secondary">Loading…</p>
      ) : allocation.length === 0 ? (
        <p className="text-sm text-text-secondary">No agent steps to display.</p>
      ) : (
        <ol className="list-none p-0 m-0">
          {allocation.map((step) => (
            <FleetStepRow
              key={step.step_id}
              step={step}
              liveEvent={liveEventsByStep[step.step_id]}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
