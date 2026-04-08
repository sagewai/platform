'use client';

import { useEffect, useRef } from 'react';

export interface TVEvent {
  id: string;
  type: string;
  message: string;
  timestamp: string;
}

const statusColor: Record<string, string> = {
  workflow_completed: 'bg-emerald-400',
  workflow_finished: 'bg-emerald-400',
  workflow_failed: 'bg-red-400',
  approval_requested: 'bg-amber-400',
  workflow_started: 'bg-blue-400',
  step_completed: 'bg-emerald-400/60',
  step_failed: 'bg-red-400/60',
};

export function TVEventFeed({ events }: { events: TVEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  return (
    <div className="flex flex-col h-full p-8">
      <h2 className="text-lg font-semibold text-white/60 uppercase tracking-widest mb-4">
        Live Event Feed
      </h2>
      <div className="flex-1 overflow-y-auto font-[family-name:var(--font-jetbrains)] text-sm space-y-1">
        {events.length === 0 && (
          <div className="text-white/30 text-center mt-20">Waiting for events...</div>
        )}
        {events.map((ev) => (
          <div key={ev.id} className="flex items-start gap-3 py-1">
            <span className="text-white/30 shrink-0 tabular-nums">{ev.timestamp}</span>
            <span
              className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${statusColor[ev.type] ?? 'bg-white/40'}`}
            />
            <span className="text-white/80">{ev.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
