'use client';

import { use, useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type {
  AutopilotMissionDetail,
  AutopilotMissionTrace,
  MissionRunEvent,
} from '@/utils/types';
import { MissionDetailView } from '@/components/autopilot/mission-detail-view';
import { AutopilotBreadcrumbs } from '@/components/autopilot-nav';

type PageProps = { params: Promise<{ id: string }> };

const NEEDS_TRACE_STATUSES = new Set([
  'running',
  'completed',
  'failed',
  'cancelled',
]);

export default function AutopilotMissionDetailPage({ params }: PageProps) {
  const { id } = use(params);
  const [mission, setMission] = useState<AutopilotMissionDetail | null>(null);
  const [trace, setTrace] = useState<AutopilotMissionTrace | null>(null);
  const [error, setError] = useState<'not_found' | 'failed' | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMission(null);
    setTrace(null);
    setError(null);

    adminApi
      .getAutopilotMission(id)
      .then((m) => {
        if (cancelled) return;
        setMission(m);
        // Fetch trace for active/terminal missions so reload-during-run works.
        if (NEEDS_TRACE_STATUSES.has(m.status)) {
          adminApi
            .getAutopilotMissionTrace(id)
            .then((t) => {
              if (!cancelled) setTrace(t);
            })
            .catch(() => {
              // Trace fetch failure is non-fatal — the SSE stream will fill in.
            });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(/404|not found/i.test(msg) ? 'not_found' : 'failed');
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  const traceEvents: MissionRunEvent[] = trace?.events ?? [];
  const traceOutput: unknown = trace?.output ?? null;

  function handleRunStarted() {
    // Refetch mission + trace so the UI transitions from directions → live trace.
    adminApi.getAutopilotMission(id).then((m) => {
      setMission(m);
      if (NEEDS_TRACE_STATUSES.has(m.status)) {
        adminApi
          .getAutopilotMissionTrace(id)
          .then((t) => setTrace(t))
          .catch(() => {
            // Non-fatal — SSE stream will fill in.
          });
      }
    }).catch(() => {
      // Non-fatal — the button already transitioned the run; leave existing state.
    });
  }

  return (
    <div>
      <AutopilotBreadcrumbs
        trail={[
          { label: 'Autopilot', href: '/autopilot' },
          { label: 'Missions', href: '/autopilot/missions' },
          { label: mission ? mission.goal_text || mission.id : id },
        ]}
      />
      {error === 'not_found' && <MissionNotFoundInline id={id} />}
      {error === 'failed' && (
        <p className="text-sm text-text-secondary">
          Could not load mission: {id}.
        </p>
      )}
      {!error && !mission && <MissionDetailSkeleton />}
      {!error && mission && (
        <MissionDetailView
          mission={mission}
          traceEvents={traceEvents}
          traceOutput={traceOutput}
          onRunStarted={handleRunStarted}
        />
      )}
    </div>
  );
}

function MissionDetailSkeleton() {
  return (
    <div className="p-6 space-y-4" data-testid="mission-detail-skeleton">
      <div className="h-8 w-1/3 rounded bg-bg-subtle animate-pulse" />
      <div className="h-[480px] w-full rounded bg-bg-subtle animate-pulse" />
      <div className="h-32 w-full rounded bg-bg-subtle animate-pulse" />
    </div>
  );
}

function MissionNotFoundInline({ id }: { id: string }) {
  return (
    <div className="p-6 flex flex-col gap-2">
      <h1 className="text-lg font-semibold text-text-primary">Mission not found</h1>
      <p className="text-text-secondary text-sm">
        Mission <code className="font-mono">{id}</code> does not exist or has been deleted.
      </p>
    </div>
  );
}
