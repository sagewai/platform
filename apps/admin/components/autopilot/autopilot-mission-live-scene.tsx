'use client';

import { useCallback, useState } from 'react';
import { Forward } from 'lucide-react';
import type { AutopilotAgentGraphJSON, MissionRunEvent } from '@/utils/types';
import {
  MissionEventProvider,
  type ReplayScheduler,
} from '@/lib/mission-events/provider';
import { AnimatedAgentGraph } from './animated-agent-graph';
import { CostBurnDownChart } from './cost-burn-down-chart';
import { MissionStatusAnnouncer } from './mission-status-announcer';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

export function AutopilotMissionLiveScene({
  missionId,
  graph,
  capUsd,
  status,
  replayEvents,
  replaySpeed,
}: {
  missionId: string;
  graph: AutopilotAgentGraphJSON;
  capUsd: number | null | undefined;
  status: string;
  replayEvents?: readonly MissionRunEvent[];
  /** Replay speed multiplier — default 4. Scrubber lifts this so speed buttons
   *  affect both the graph animation and the snapshot panel. */
  replaySpeed?: number;
}) {
  const isTerminal = TERMINAL_STATUSES.has(status);
  const [scheduler, setScheduler] = useState<ReplayScheduler>(null);
  const [skipped, setSkipped] = useState(false);

  const handleScheduler = useCallback(
    (s: ReplayScheduler) => setScheduler(s),
    [],
  );

  const effectiveReplay =
    isTerminal && replayEvents && replayEvents.length > 0
      ? replayEvents
      : undefined;

  return (
    <MissionEventProvider
      missionId={missionId}
      liveStream={!isTerminal}
      replayEvents={effectiveReplay}
      replaySpeed={replaySpeed}
      onReplayScheduler={handleScheduler}
    >
      <div className="flex flex-col gap-4" data-testid="mission-live-scene">
        <div className="relative rounded-lg border border-border bg-bg-surface p-2">
          <AnimatedAgentGraph graph={graph} />
          {scheduler && !skipped && (
            <button
              type="button"
              data-testid="skip-replay"
              onClick={() => {
                scheduler.flush();
                setSkipped(true);
              }}
              className="absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded bg-bg-surface/95 px-2.5 py-1 text-xs font-medium text-text-primary ring-1 ring-border shadow-sm hover:bg-bg-subtle transition-colors"
            >
              <Forward className="size-3" aria-hidden />
              Skip replay
            </button>
          )}
        </div>
        <CostBurnDownChart capUsd={capUsd} />
        <MissionStatusAnnouncer />
      </div>
    </MissionEventProvider>
  );
}
