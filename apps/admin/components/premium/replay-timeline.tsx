'use client';

import type { WorkflowEvent } from '@/utils/types';

interface Props {
  events: WorkflowEvent[];
  currentIndex: number;
  onSeek: (index: number) => void;
}

function eventColor(type: string): string {
  if (type === 'step_completed' || type === 'workflow_finished') return '#22c55e';
  if (type === 'workflow_failed') return '#ef4444';
  if (type === 'step_started' || type === 'workflow_started') return '#3b82f6';
  if (type.includes('approval') || type.includes('waiting')) return '#f59e0b';
  return '#6b7280';
}

export function ReplayTimeline({ events, currentIndex, onSeek }: Props) {
  if (events.length === 0) return null;

  const totalWidth = events.length;

  return (
    <div className="w-full">
      <div className="relative h-10 bg-bg-subtle rounded-lg border border-border overflow-hidden">
        {/* Event ticks */}
        <div className="absolute inset-0 flex items-center px-2">
          {events.map((evt, i) => {
            const left = totalWidth <= 1 ? 50 : (i / (totalWidth - 1)) * 100;
            return (
              <button
                key={i}
                type="button"
                onClick={() => onSeek(i)}
                className="absolute w-3 h-3 rounded-full border-2 border-bg-surface cursor-pointer hover:scale-125 transition-transform z-10"
                style={{
                  left: `${left}%`,
                  backgroundColor: eventColor(evt.event_type),
                  transform: `translateX(-50%)${i === currentIndex ? ' scale(1.4)' : ''}`,
                }}
                title={`${evt.event_type} (${i + 1}/${totalWidth})`}
              />
            );
          })}
        </div>

        {/* Progress bar */}
        <div
          className="absolute bottom-0 left-0 h-1 bg-primary transition-all duration-200"
          style={{
            width: totalWidth <= 1 ? '100%' : `${(currentIndex / (totalWidth - 1)) * 100}%`,
          }}
        />

        {/* Current position marker */}
        <div
          className="absolute top-0 h-full w-0.5 bg-primary z-20 transition-all duration-200"
          style={{
            left:
              totalWidth <= 1
                ? '50%'
                : `${(currentIndex / (totalWidth - 1)) * 100}%`,
          }}
        />
      </div>

      {/* Labels */}
      <div className="flex justify-between text-[10px] text-text-muted mt-1 px-1">
        <span>Event 1</span>
        <span>
          {currentIndex + 1} / {totalWidth}
        </span>
        <span>Event {totalWidth}</span>
      </div>
    </div>
  );
}
