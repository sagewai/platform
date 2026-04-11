'use client';

import { useEffect, useRef } from 'react';
import type { AGUIEvent } from '@/utils/agui-types';
import { EVENT_COLORS } from '@/utils/agui-types';
import { EventBadge } from './event-badge';
import { ToolCallDetail } from './tool-call-detail';
import { StateViewer } from './state-viewer';

interface EventTimelineProps {
  events: AGUIEvent[];
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

function EventDetail({ event }: { event: AGUIEvent }) {
  const { type, data } = event;

  if (type === 'run_started') {
    return (
      <span className="text-text-secondary">
        Agent <strong>{event.agent_name}</strong> started
        {data.input ? `: "${String(data.input).slice(0, 80)}"` : ''}
      </span>
    );
  }

  if (type === 'text_message_content') {
    return (
      <span className="text-success text-xs font-[family-name:var(--font-mono)]">
        {String(data.delta ?? data.content ?? '')}
      </span>
    );
  }

  if (type === 'text_message_start' || type === 'text_message_end') {
    return <span className="text-text-muted text-xs">{type.replace(/_/g, ' ')}</span>;
  }

  if (type === 'tool_call_start') {
    return (
      <ToolCallDetail
        toolName={String(data.tool_name ?? 'unknown')}
        arguments_={(data.arguments as Record<string, unknown>) ?? {}}
      />
    );
  }

  if (type === 'tool_call_end') {
    return (
      <ToolCallDetail
        toolName={String(data.tool_name ?? 'unknown')}
        arguments_={{}}
        result={data.result}
        durationMs={data.duration_ms as number | undefined}
      />
    );
  }

  if (type === 'state_snapshot') {
    return <StateViewer data={(data as Record<string, unknown>) ?? {}} />;
  }

  if (type === 'state_delta') {
    return <StateViewer data={(data as Record<string, unknown>) ?? {}} isDelta />;
  }

  if (type === 'step') {
    return (
      <span className="text-text-muted text-xs">
        {String(data.step_type ?? 'step')}: {String(data.detail ?? '')}
        {data.duration_ms != null && (
          <span className="ml-1.5 text-text-muted">{String(data.duration_ms)}ms</span>
        )}
      </span>
    );
  }

  if (type === 'run_finished') {
    return (
      <span className="text-info">
        Completed — {String(data.total_tokens ?? 0)} tokens
        {data.output ? `, output: "${String(data.output).slice(0, 60)}"` : ''}
      </span>
    );
  }

  if (type === 'run_error') {
    return <span className="text-error font-semibold">{String(data.error ?? 'Unknown error')}</span>;
  }

  return <span className="text-text-muted text-xs">{JSON.stringify(data)}</span>;
}

export function EventTimeline({ events }: EventTimelineProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  if (events.length === 0) {
    return (
      <div className="text-text-muted text-center p-10">
        No events yet. Select a run or start demo mode.
      </div>
    );
  }

  return (
    <div className="relative pl-6">
      {/* Vertical timeline line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-0.5 bg-border" />
      {events.map((event, i) => {
        const color = EVENT_COLORS[event.type] ?? '#6b7280';
        return (
          <div
            key={i}
            className="relative pb-4 pl-4"
          >
            {/* Timeline dot — keep style for dynamic EVENT_COLORS */}
            <div
              className="absolute -left-5 top-1.5 w-3 h-3 rounded-full border-2 border-white shadow-[0_0_0_1px_var(--color-border)]"
              style={{ background: color }}
            />
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] text-text-muted font-[family-name:var(--font-mono)]">
                {formatTime(event.timestamp)}
              </span>
              <EventBadge type={event.type} />
            </div>
            <div className="ml-1">
              <EventDetail event={event} />
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
