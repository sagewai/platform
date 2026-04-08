'use client';

import type { QueueStats, WorkerInfo } from '@/utils/types';

interface Props {
  stats: QueueStats | null;
  workers: WorkerInfo[];
}

interface WorkflowRow {
  name: string;
  active: number;
  queued: number;
  failed: number;
  status: 'healthy' | 'degraded' | 'failing';
}

const statusDot: Record<string, string> = {
  healthy: 'bg-emerald-400',
  degraded: 'bg-amber-400',
  failing: 'bg-red-400',
};

export function TVStatusBoard({ stats, workers }: Props) {
  // Build rows from available data
  const rows: WorkflowRow[] = [];

  if (stats) {
    const failed = stats.failed ?? 0;
    const total = stats.total ?? 0;
    const errorRate = total > 0 ? failed / total : 0;
    let status: 'healthy' | 'degraded' | 'failing' = 'healthy';
    if (errorRate > 0.2) status = 'failing';
    else if (errorRate > 0.05 || stats.pending > 50) status = 'degraded';

    rows.push({
      name: 'All Workflows',
      active: stats.running ?? 0,
      queued: stats.pending ?? 0,
      failed,
      status,
    });

    // If we have workers, show per-worker activity
    workers.forEach((w) => {
      rows.push({
        name: `Worker ${w.owner_id.slice(0, 8)}`,
        active: w.active_runs,
        queued: 0,
        failed: 0,
        status: w.active_runs > 0 ? 'healthy' : 'degraded',
      });
    });
  }

  return (
    <div className="flex flex-col h-full p-8">
      <h2 className="text-lg font-semibold text-white/60 uppercase tracking-widest mb-6">
        Workflow Status Board
      </h2>
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-white/40 uppercase tracking-wider text-xs">
              <th className="text-left py-3 px-4">Workflow</th>
              <th className="text-right py-3 px-4">Active</th>
              <th className="text-right py-3 px-4">Queued</th>
              <th className="text-right py-3 px-4">Failed (24h)</th>
              <th className="text-center py-3 px-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="text-center py-12 text-white/30">
                  No workflow data available
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.name} className="border-b border-white/5 hover:bg-white/5">
                <td className="py-3 px-4 text-white/90 font-medium">{row.name}</td>
                <td className="py-3 px-4 text-right text-white/70 tabular-nums">{row.active}</td>
                <td className="py-3 px-4 text-right text-white/70 tabular-nums">{row.queued}</td>
                <td className="py-3 px-4 text-right text-white/70 tabular-nums">{row.failed}</td>
                <td className="py-3 px-4 text-center">
                  <span className="inline-flex items-center gap-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${statusDot[row.status]}`} />
                    <span className="text-white/50 capitalize">{row.status}</span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
