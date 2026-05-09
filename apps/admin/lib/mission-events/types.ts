// Plan N event types — canonical re-export of the Plan H MissionRunEvent shape
// plus a synthesized `agent.handoff` kind that DataFlowParticles emits internally
// when it detects a finish-then-start across a known edge.

import type { MissionRunEvent } from '@/utils/types';

export type { MissionRunEvent };

// Alias for callers that prefer the plan-N name.
export type MissionEvent = MissionRunEvent;

export interface AgentHandoff {
  fromAgent: string;
  toAgent: string;
  ts: string;
}
