// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { MissionRunEvent } from '@/utils/types';

export type ReplaySpeed = 0.5 | 1 | 2 | 4;

export interface TraceReplayApi {
  currentIndex: number;
  isPlaying: boolean;
  speed: ReplaySpeed;
  play: () => void;
  pause: () => void;
  seek: (index: number) => void;
  setSpeed: (speed: ReplaySpeed) => void;
}

/** Client-side trace scrubber — no backend round-trips after the initial /trace fetch. */
export function useTraceReplay(events: MissionRunEvent[]): TraceReplayApi {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const eventsRef = useRef(events);
  eventsRef.current = events;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!isPlaying) return;
    const evts = eventsRef.current;
    if (currentIndex >= evts.length - 1) {
      setIsPlaying(false);
      return;
    }
    const here = new Date(evts[currentIndex].ts).getTime();
    const next = new Date(evts[currentIndex + 1].ts).getTime();
    const delayMs = Math.max(50, (next - here) / speed);
    timerRef.current = setTimeout(() => setCurrentIndex((i: number) => i + 1), delayMs);
    return clearTimer;
  }, [isPlaying, currentIndex, speed, clearTimer]);

  const play = useCallback(() => {
    if (eventsRef.current.length === 0) return;
    if (currentIndex >= eventsRef.current.length - 1) {
      setCurrentIndex(0);
    }
    setIsPlaying(true);
  }, [currentIndex]);

  const pause = useCallback(() => {
    clearTimer();
    setIsPlaying(false);
  }, [clearTimer]);

  const seek = useCallback(
    (index: number) => {
      clearTimer();
      setIsPlaying(false);
      setCurrentIndex(Math.max(0, Math.min(eventsRef.current.length - 1, index)));
    },
    [clearTimer],
  );

  return { currentIndex, isPlaying, speed, play, pause, seek, setSpeed };
}
