'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { GitBranch } from 'lucide-react';
import { Button } from '@/components/ui/legacy';
import type { WorkflowEvent } from '@/utils/types';
import { ExecutionCanvas } from './execution-canvas';
import { ReplayTimeline } from './replay-timeline';
import { ReplayControls } from './replay-controls';
import { ReplayForkDialog } from './replay-fork-dialog';

interface Props {
  runId: string;
  workflowDefinition?: Record<string, unknown> | null;
  events: WorkflowEvent[];
}

export function ReplayPlayer({ runId, workflowDefinition, events }: Props) {
  const [currentIndex, setCurrentIndex] = useState(events.length - 1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [forkEvent, setForkEvent] = useState<WorkflowEvent | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const totalEvents = events.length;
  const currentEvents = events.slice(0, currentIndex + 1);

  // Auto-pause at failed events
  useEffect(() => {
    if (currentIndex < totalEvents) {
      const evt = events[currentIndex];
      if (evt?.event_type === 'workflow_failed') {
        setIsPlaying(false);
      }
    }
  }, [currentIndex, events, totalEvents]);

  // Playback interval
  useEffect(() => {
    if (isPlaying && currentIndex < totalEvents - 1) {
      const interval = 1000 / speed;
      intervalRef.current = setInterval(() => {
        setCurrentIndex((prev) => {
          if (prev >= totalEvents - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, interval);
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isPlaying, speed, totalEvents, currentIndex]);

  // Stop playing when we reach the end
  useEffect(() => {
    if (currentIndex >= totalEvents - 1) {
      setIsPlaying(false);
    }
  }, [currentIndex, totalEvents]);

  const handleTogglePlay = useCallback(() => {
    if (currentIndex >= totalEvents - 1) {
      // Restart from beginning
      setCurrentIndex(0);
      setIsPlaying(true);
    } else {
      setIsPlaying((p) => !p);
    }
  }, [currentIndex, totalEvents]);

  const handleStepBack = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex((prev) => Math.max(0, prev - 1));
  }, []);

  const handleStepForward = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex((prev) => Math.min(totalEvents - 1, prev + 1));
  }, [totalEvents]);

  const handleSkipToStart = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex(0);
  }, []);

  const handleSkipToEnd = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex(totalEvents - 1);
  }, [totalEvents]);

  const handleSeek = useCallback(
    (index: number) => {
      setIsPlaying(false);
      setCurrentIndex(Math.max(0, Math.min(index, totalEvents - 1)));
    },
    [totalEvents],
  );

  const handleForkFromHere = useCallback(() => {
    if (currentIndex < totalEvents) {
      setForkEvent(events[currentIndex]);
    }
  }, [currentIndex, events, totalEvents]);

  if (totalEvents === 0) {
    return (
      <div className="text-sm text-text-muted text-center py-8">
        No events available for replay.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Canvas */}
      <ExecutionCanvas
        runId={runId}
        workflowDefinition={workflowDefinition}
        events={currentEvents}
        isLive={false}
      />

      {/* Timeline */}
      <ReplayTimeline events={events} currentIndex={currentIndex} onSeek={handleSeek} />

      {/* Controls */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <ReplayControls
          isPlaying={isPlaying}
          speed={speed}
          canStepBack={currentIndex > 0}
          canStepForward={currentIndex < totalEvents - 1}
          onSkipToStart={handleSkipToStart}
          onStepBack={handleStepBack}
          onTogglePlay={handleTogglePlay}
          onStepForward={handleStepForward}
          onSkipToEnd={handleSkipToEnd}
          onSpeedChange={setSpeed}
        />

        <Button variant="secondary" size="sm" onClick={handleForkFromHere}>
          <GitBranch size={13} className="mr-1" />
          Fork from here
        </Button>
      </div>

      {/* Current event info */}
      <div className="text-xs text-text-muted bg-bg-subtle rounded p-2 font-[family-name:var(--font-mono)]">
        <span className="text-text-primary font-medium">
          [{currentIndex + 1}/{totalEvents}]
        </span>{' '}
        {events[currentIndex]?.event_type.replace(/_/g, ' ')}
        {typeof events[currentIndex]?.data?.agent === 'string' && (
          <span className="text-primary ml-1">
            ({events[currentIndex].data.agent as string})
          </span>
        )}
      </div>

      {/* Fork dialog */}
      <ReplayForkDialog event={forkEvent} onClose={() => setForkEvent(null)} />
    </div>
  );
}
