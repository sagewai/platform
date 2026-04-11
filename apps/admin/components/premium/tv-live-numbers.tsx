'use client';

import { useEffect, useRef, useState } from 'react';
import type { QueueStats, WorkerInfo } from '@/utils/types';

interface Props {
  stats: QueueStats | null;
  workers: WorkerInfo[];
}

function AnimatedNumber({ value, label, color }: { value: number; label: string; color?: string }) {
  const [display, setDisplay] = useState(0);
  const prev = useRef(0);

  useEffect(() => {
    const start = prev.current;
    const diff = value - start;
    if (diff === 0) return;
    const steps = 20;
    let step = 0;
    const timer = setInterval(() => {
      step++;
      setDisplay(Math.round(start + (diff * step) / steps));
      if (step >= steps) {
        clearInterval(timer);
        setDisplay(value);
        prev.current = value;
      }
    }, 30);
    return () => clearInterval(timer);
  }, [value]);

  return (
    <div className="flex flex-col items-center justify-center rounded-2xl bg-white/5 border border-white/10 p-8">
      <span className={`text-7xl font-bold tabular-nums ${color ?? 'text-[#26C6DA]'}`}>
        {display.toLocaleString()}
      </span>
      <span className="mt-3 text-sm uppercase tracking-widest text-white/50">{label}</span>
    </div>
  );
}

export function TVLiveNumbers({ stats, workers }: Props) {
  const activeWorkers = workers.length;
  const queueDepth = stats?.pending ?? 0;
  const throughput = stats?.completed ?? 0;
  const total = stats?.total ?? 0;
  const errorRate = total > 0 ? Math.round(((stats?.failed ?? 0) / total) * 100) : 0;

  return (
    <div className="grid grid-cols-2 gap-8 h-full p-12 content-center">
      <AnimatedNumber value={activeWorkers} label="Active Workers" />
      <AnimatedNumber value={queueDepth} label="Queue Depth" color="text-[#FFB74D]" />
      <AnimatedNumber value={throughput} label="Completed (Total)" />
      <AnimatedNumber
        value={errorRate}
        label="Error Rate %"
        color={errorRate > 10 ? 'text-red-400' : 'text-emerald-400'}
      />
    </div>
  );
}
