'use client';

import { useEffect, useState } from 'react';
import { useMissionEvent } from '@/lib/mission-events/provider';

export function MissionStatusAnnouncer() {
  const event = useMissionEvent();
  const [msg, setMsg] = useState('');

  useEffect(() => {
    if (!event) return;
    const owner = event.node_id ?? event.agent_id ?? 'agent';
    if (event.kind === 'agent.started') {
      setMsg(`${owner} started`);
    } else if (event.kind === 'agent.finished') {
      setMsg(
        `${owner} ${event.status === 'failed' ? 'failed' : 'completed'}`,
      );
    } else if (event.kind === 'agent.tool_failed') {
      setMsg(`${owner} tool failed`);
    } else if (event.kind === 'mission.finished') {
      setMsg(`Mission ${event.status ?? 'completed'}`);
    }
  }, [event]);

  return (
    <div
      data-testid="mission-status-announcer"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
    >
      {msg}
    </div>
  );
}
