/** AG-UI event type definitions for live execution monitoring. */

export type AGUIEventType =
  | 'run_started'
  | 'text_message_start'
  | 'text_message_content'
  | 'text_message_end'
  | 'tool_call_start'
  | 'tool_call_end'
  | 'state_snapshot'
  | 'state_delta'
  | 'step'
  | 'run_finished'
  | 'run_error';

export interface AGUIEvent {
  type: AGUIEventType;
  timestamp: string;
  run_id: string;
  agent_name?: string;
  data: Record<string, unknown>;
}

export interface RunTimeline {
  run_id: string;
  agent_name: string;
  status: 'running' | 'completed' | 'error';
  events: AGUIEvent[];
  started_at: string;
  finished_at?: string;
}

export const EVENT_COLORS: Record<string, string> = {
  run_started: '#3b82f6',
  run_finished: '#3b82f6',
  text_message_start: '#10b981',
  text_message_content: '#10b981',
  text_message_end: '#10b981',
  tool_call_start: '#8b5cf6',
  tool_call_end: '#8b5cf6',
  state_snapshot: '#f59e0b',
  state_delta: '#f59e0b',
  step: '#6b7280',
  run_error: '#ef4444',
};
