'use client';

import type { AutopilotMissionDetail, MissionRunEvent } from '@/utils/types';
import { AutopilotMissionHeader } from './autopilot-mission-header';
import { AutopilotAgentGraph } from './autopilot-agent-graph';
import { AutopilotMissionLiveScene } from './autopilot-mission-live-scene';
import { AutopilotResourcePanels } from './autopilot-resource-panels';
import { AutopilotDirections } from './autopilot-directions';
import { AutopilotMissionLiveTrace } from './autopilot-mission-live-trace';
import { AutopilotMissionOutput } from './autopilot-mission-output';
import { AutopilotSandboxPanel } from './autopilot-sandbox-panel';
import { AutopilotFleetPanel } from './autopilot-fleet-panel';
import { AutopilotSealedPanel } from './autopilot-sealed-panel';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
const ACTIVE_STATUSES = new Set(['running', 'completed', 'failed', 'cancelled']);

export function MissionDetailView({
  mission,
  traceEvents = [],
  traceOutput = null,
  onRunStarted,
}: {
  mission: AutopilotMissionDetail;
  traceEvents?: MissionRunEvent[];
  traceOutput?: unknown;
  onRunStarted?: () => void;
}) {
  const isActive = ACTIVE_STATUSES.has(mission.status);
  const isCompleted = mission.status === 'completed';
  const isTerminal = TERMINAL_STATUSES.has(mission.status);
  const showLiveScene = mission.status === 'running' || isTerminal;
  const capUsd = mission.estimated_cost?.amount ?? null;

  const liveEventsByStep = Object.fromEntries(
    traceEvents
      .filter((e) =>
        e.kind === 'agent.dispatched_to_worker' ||
        e.kind === 'agent.worker_claimed' ||
        e.kind === 'agent.no_worker_available'
      )
      .reduce((map, e) => {
        const key = e.step_id ?? e.node_id;
        if (key) map.set(key, e);
        return map;
      }, new Map<string, MissionRunEvent>())
  ) as Record<string, { kind: string; worker_id?: string; worker_name?: string; queue_position?: number }>;

  return (
    <div className="flex flex-col gap-6 p-6">
      <AutopilotMissionHeader mission={mission} onRunStarted={onRunStarted} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          {showLiveScene ? (
            <AutopilotMissionLiveScene
              missionId={mission.id}
              graph={mission.agent_graph_json}
              capUsd={capUsd}
              status={mission.status}
              replayEvents={isTerminal ? traceEvents : []}
            />
          ) : (
            <div className="rounded-lg border border-border bg-bg-surface p-4">
              <AutopilotAgentGraph graph={mission.agent_graph_json} />
            </div>
          )}
        </div>
        <div className="flex flex-col gap-4">
          <AutopilotResourcePanels mission={mission} />
        </div>
      </div>
      <div className="rounded-lg border border-border bg-bg-surface p-4">
        {isActive ? (
          <AutopilotMissionLiveTrace
            missionId={mission.id}
            initialEvents={traceEvents}
            initialOutput={traceOutput}
            initialStatus={
              isTerminal
                ? (mission.status as 'completed' | 'failed' | 'cancelled')
                : 'running'
            }
          />
        ) : (
          <AutopilotDirections missionId={mission.id} />
        )}
      </div>
      <AutopilotSandboxPanel missionId={mission.id} />
      <AutopilotFleetPanel
        missionId={mission.id}
        missionStatus={mission.status}
        liveEventsByStep={liveEventsByStep}
      />
      <AutopilotSealedPanel missionId={mission.id} />
      {isCompleted && traceOutput != null && (
        <AutopilotMissionOutput output={traceOutput} />
      )}
    </div>
  );
}
