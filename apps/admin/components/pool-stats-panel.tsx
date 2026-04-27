'use client';

import { useEffect, useState } from 'react';
import { Database } from 'lucide-react';
import { Card } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type { PoolStatsSnapshot } from '@/utils/types';

interface PoolStatsPanelProps {
  workerId: string;
}

function fmtRate(r: number | null): string {
  return r === null ? '—' : `${(r * 100).toFixed(0)}%`;
}

export function PoolStatsPanel({ workerId }: PoolStatsPanelProps) {
  const [data, setData] = useState<PoolStatsSnapshot | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = () => {
      adminApi
        .getWorkerPoolStats(workerId)
        .then((snap) => {
          if (!cancelled) {
            setData(snap);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(String(err));
        });
    };

    load();
    const interval = setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [workerId]);

  if (data === undefined && error === null) {
    return (
      <Card className="!p-5 animate-pulse">
        <div className="h-3 w-32 bg-bg-subtle rounded mb-2" />
        <div className="h-3 w-24 bg-bg-subtle rounded" />
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="!p-5">
        <div className="flex items-center gap-2 mb-2">
          <Database size={16} className="text-text-muted" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
            Sandbox Pool
          </h3>
        </div>
        <p className="text-sm text-error m-0">Failed to load pool stats: {error}</p>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card className="!p-5">
        <div className="flex items-center gap-2 mb-2">
          <Database size={16} className="text-text-muted" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
            Sandbox Pool
          </h3>
        </div>
        <p className="text-sm text-text-muted m-0">
          No pool stats reported yet. The worker either has not run a sandboxed step
          yet or is using a provider-managed pool.
        </p>
      </Card>
    );
  }

  return (
    <Card className="!p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database size={16} className="text-text-muted" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
            Sandbox Pool
          </h3>
        </div>
        <span className="text-xs text-text-muted">
          Snapshot {new Date(data.captured_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Aggregate row */}
      <div>
        <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">Aggregate</div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-text-muted">Warm slots</dt>
          <dd className="m-0">
            {data.aggregate.warm_count}/{data.aggregate.warm_max_global}
          </dd>
          <dt className="text-text-muted">Active</dt>
          <dd className="m-0">{data.aggregate.active_count}</dd>
          <dt className="text-text-muted">Hit rate (1h)</dt>
          <dd className="m-0">{fmtRate(data.aggregate.hit_rate_1h)}</dd>
          <dt className="text-text-muted">Last evict</dt>
          <dd className="m-0">
            {data.aggregate.last_evict_at
              ? new Date(data.aggregate.last_evict_at).toLocaleTimeString()
              : '—'}
          </dd>
        </dl>
      </div>

      {/* Per-tuple table */}
      {data.per_tuple.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">Per tuple</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-text-muted border-b border-border">
                  <th className="pb-1 font-medium">Image</th>
                  <th className="pb-1 font-medium">Exec</th>
                  <th className="pb-1 font-medium">Network</th>
                  <th className="pb-1 font-medium text-right">Warm</th>
                  <th className="pb-1 font-medium text-right">Active</th>
                  <th className="pb-1 font-medium text-right">Hit</th>
                  <th className="pb-1 font-medium">Last evict reason</th>
                </tr>
              </thead>
              <tbody>
                {data.per_tuple.map((t, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1">{t.image_variant}</td>
                    <td className="py-1">{t.execution_mode}</td>
                    <td className="py-1">{t.network_policy}</td>
                    <td className="py-1 text-right">
                      {t.warm_count}/{t.warm_max}
                    </td>
                    <td className="py-1 text-right">{t.active_count}</td>
                    <td className="py-1 text-right">{fmtRate(t.hit_rate_1h)}</td>
                    <td className="py-1 text-text-muted">{t.last_evict_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}
