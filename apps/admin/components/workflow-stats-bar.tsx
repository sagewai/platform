'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { adminApi } from '@/utils/api';
import type { QueueStats } from '@/utils/types';

const STAT_ITEMS: Array<{
  key: keyof Omit<QueueStats, 'total'>;
  label: string;
  color: string;
  status: string;
}> = [
  { key: 'pending', label: 'Pending', color: 'text-blue-400', status: 'pending' },
  { key: 'running', label: 'Running', color: 'text-cyan-400', status: 'running' },
  { key: 'completed', label: 'Completed', color: 'text-success', status: 'completed' },
  { key: 'failed', label: 'Failed', color: 'text-error', status: 'failed' },
  { key: 'waiting', label: 'Waiting', color: 'text-amber-400', status: 'waiting' },
];

export function WorkflowStatsBar() {
  const [stats, setStats] = useState<QueueStats | null>(null);

  useEffect(() => {
    const fetch = () => {
      adminApi.getWorkflowStats().then(setStats).catch(() => {});
    };
    fetch();
    const interval = setInterval(fetch, 10000);
    return () => clearInterval(interval);
  }, []);

  if (!stats) {
    return (
      <div className="grid grid-cols-5 gap-md mb-lg">
        {STAT_ITEMS.map((s) => (
          <div key={s.key} className="bg-bg-surface rounded-lg border border-border p-md animate-pulse">
            <div className="h-3 w-16 bg-bg-subtle rounded mb-2" />
            <div className="h-7 w-10 bg-bg-subtle rounded" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div data-tour="workflow-stats" className="grid grid-cols-5 gap-md mb-lg">
      {STAT_ITEMS.map((s) => (
        <Link
          key={s.key}
          href={`/workflows/history?status=${s.status}`}
          className="bg-bg-surface rounded-lg border border-border p-md no-underline text-inherit hover:border-primary transition-colors"
        >
          <div className="text-xs text-text-muted uppercase tracking-wide">{s.label}</div>
          <div className={`text-2xl font-bold mt-1 font-[family-name:var(--font-heading)] ${s.color}`}>
            {stats[s.key]}
          </div>
        </Link>
      ))}
    </div>
  );
}
