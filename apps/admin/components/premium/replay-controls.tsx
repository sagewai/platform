'use client';

import {
  SkipBack,
  ChevronLeft,
  Pause,
  Play,
  ChevronRight,
  SkipForward,
} from 'lucide-react';

interface Props {
  isPlaying: boolean;
  speed: number;
  canStepBack: boolean;
  canStepForward: boolean;
  onSkipToStart: () => void;
  onStepBack: () => void;
  onTogglePlay: () => void;
  onStepForward: () => void;
  onSkipToEnd: () => void;
  onSpeedChange: (speed: number) => void;
}

const SPEEDS = [0.5, 1, 2, 5, 10];

export function ReplayControls({
  isPlaying,
  speed,
  canStepBack,
  canStepForward,
  onSkipToStart,
  onStepBack,
  onTogglePlay,
  onStepForward,
  onSkipToEnd,
  onSpeedChange,
}: Props) {
  return (
    <div className="flex items-center gap-2">
      {/* VCR buttons */}
      <div className="flex items-center gap-1 bg-bg-subtle rounded-lg border border-border p-1">
        <button
          type="button"
          onClick={onSkipToStart}
          disabled={!canStepBack}
          className="p-1.5 rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed border-none bg-transparent cursor-pointer text-text-primary"
          title="Skip to start"
        >
          <SkipBack size={14} />
        </button>
        <button
          type="button"
          onClick={onStepBack}
          disabled={!canStepBack}
          className="p-1.5 rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed border-none bg-transparent cursor-pointer text-text-primary"
          title="Step back"
        >
          <ChevronLeft size={14} />
        </button>
        <button
          type="button"
          onClick={onTogglePlay}
          className="p-1.5 rounded hover:bg-bg-surface border-none bg-transparent cursor-pointer text-primary"
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button
          type="button"
          onClick={onStepForward}
          disabled={!canStepForward}
          className="p-1.5 rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed border-none bg-transparent cursor-pointer text-text-primary"
          title="Step forward"
        >
          <ChevronRight size={14} />
        </button>
        <button
          type="button"
          onClick={onSkipToEnd}
          disabled={!canStepForward}
          className="p-1.5 rounded hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed border-none bg-transparent cursor-pointer text-text-primary"
          title="Skip to end"
        >
          <SkipForward size={14} />
        </button>
      </div>

      {/* Speed selector */}
      <div className="flex items-center gap-1 bg-bg-subtle rounded-lg border border-border p-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSpeedChange(s)}
            className={`px-2 py-1 text-[11px] font-medium rounded border-none cursor-pointer transition-colors ${
              speed === s
                ? 'bg-primary text-white'
                : 'bg-transparent text-text-muted hover:text-text-primary'
            }`}
          >
            {s}x
          </button>
        ))}
      </div>
    </div>
  );
}
