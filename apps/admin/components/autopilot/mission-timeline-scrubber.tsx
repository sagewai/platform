// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import type { ChangeEvent } from 'react';
import { Pause, Play } from 'lucide-react';
import type { MissionRunEvent } from '@/utils/types';
import type { TraceReplayApi, ReplaySpeed } from '@/hooks/use-trace-replay';

export type { ReplaySpeed };

const SPEEDS: ReplaySpeed[] = [0.5, 1, 2, 4];
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export interface MissionTimelineScrubberProps {
  missionStatus: string;
  events: MissionRunEvent[];
  replay: TraceReplayApi;
  /** Called when user drags the slider to inspect a step. */
  onScrub?: (index: number) => void;
}

export function MissionTimelineScrubber({
  missionStatus,
  events,
  replay,
  onScrub,
}: MissionTimelineScrubberProps) {
  if (!TERMINAL.has(missionStatus)) return null;
  if (events.length === 0) return null;

  const max = events.length - 1;

  const handleSlider = (e: ChangeEvent<HTMLInputElement>) => {
    const idx = Number(e.target.value);
    replay.seek(idx);
    onScrub?.(idx);
  };

  return (
    <div
      data-testid="mission-timeline-scrubber"
      className="rounded-xl border border-border bg-bg-surface p-4"
    >
      <div className="flex items-center gap-3 mb-3">
        <button
          type="button"
          data-testid={replay.isPlaying ? 'pause-replay' : 'play-replay'}
          onClick={replay.isPlaying ? replay.pause : replay.play}
          aria-label={replay.isPlaying ? 'Pause replay' : 'Play replay'}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          {replay.isPlaying ? (
            <Pause className="size-3.5" aria-hidden />
          ) : (
            <Play className="size-3.5" aria-hidden />
          )}
          {replay.isPlaying ? 'Pause' : 'Play'}
        </button>

        <div role="group" aria-label="Replay speed" className="flex gap-1">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => replay.setSpeed(s)}
              aria-pressed={replay.speed === s}
              className={`px-2 py-1 rounded text-xs font-mono transition-colors ${
                replay.speed === s
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-bg-subtle text-text-secondary hover:bg-bg-surface hover:text-text-primary'
              }`}
            >
              {s}×
            </button>
          ))}
        </div>

        <span className="ml-auto font-mono text-xs text-text-muted tabular-nums">
          {replay.currentIndex + 1}&thinsp;/&thinsp;{events.length}
        </span>
      </div>

      <input
        type="range"
        min={0}
        max={max}
        value={replay.currentIndex}
        onChange={handleSlider}
        aria-label="Mission timeline"
        data-testid="timeline-slider"
        className="w-full accent-primary cursor-pointer"
      />

      {/* Timestamp labels */}
      <div className="flex justify-between mt-1 text-[10px] text-text-muted font-mono">
        <span>{formatTs(events[0].ts)}</span>
        <span>{formatTs(events[max].ts)}</span>
      </div>
    </div>
  );
}

function formatTs(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return ts;
  }
}
