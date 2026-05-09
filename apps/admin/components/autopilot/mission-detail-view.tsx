'use client';

import type { AutopilotMissionDetail } from '@/utils/types';
import { AutopilotMissionHeader } from './autopilot-mission-header';
import { AutopilotAgentGraph } from './autopilot-agent-graph';
import { AutopilotResourcePanels } from './autopilot-resource-panels';
import { AutopilotDirections } from './autopilot-directions';

export function MissionDetailView({ mission }: { mission: AutopilotMissionDetail }) {
  return (
    <div className="flex flex-col gap-6 p-6">
      <AutopilotMissionHeader mission={mission} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 rounded-lg border border-border bg-bg-surface p-4">
          <AutopilotAgentGraph graph={mission.agent_graph_json} />
        </div>
        <div className="flex flex-col gap-4">
          <AutopilotResourcePanels mission={mission} />
        </div>
      </div>
      <div className="rounded-lg border border-border bg-bg-surface p-4">
        <AutopilotDirections missionId={mission.id} />
      </div>
    </div>
  );
}
