// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { use, useCallback, useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type {
  AutopilotMissionDetail,
  AutopilotMissionTrace,
  MissionRunEvent,
} from '@/utils/types';
import { MissionDetailView } from '@/components/autopilot/mission-detail-view';
import { MissionDetailLoadingState } from '@/components/autopilot/mission-detail-loading-state';
import { NotFoundMission } from '@/components/autopilot/not-found-mission';
import { MissionLoadError } from '@/components/autopilot/mission-load-error';
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

  const load = useCallback(
    (targetId: string) => {
      let cancelled = false;
      setMission(null);
      setTrace(null);
      setError(null);

      adminApi
        .getAutopilotMission(targetId)
        .then((m) => {
          if (cancelled) return;
          setMission(m);
          if (NEEDS_TRACE_STATUSES.has(m.status)) {
            adminApi
              .getAutopilotMissionTrace(targetId)
              .then((t) => {
                if (!cancelled) setTrace(t);
              })
              .catch(() => {
                // Non-fatal — SSE stream will fill in.
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
    },
    [],
  );

  useEffect(() => load(id), [id, load]);

  const traceEvents: MissionRunEvent[] = trace?.events ?? [];
  const traceOutput: unknown = trace?.output ?? null;

  function handleRunStarted() {
    adminApi.getAutopilotMission(id).then((m) => {
      setMission(m);
      if (NEEDS_TRACE_STATUSES.has(m.status)) {
        adminApi
          .getAutopilotMissionTrace(id)
          .then((t) => setTrace(t))
          .catch(() => {});
      }
    }).catch(() => {});
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

      {error === 'not_found' && <NotFoundMission id={id} />}
      {error === 'failed' && (
        <MissionLoadError id={id} onRetry={() => load(id)} />
      )}
      {!error && !mission && <MissionDetailLoadingState />}
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
