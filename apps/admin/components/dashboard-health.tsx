'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { startTour, shouldShowTour } from '@/utils/tours';
import type { HealthSummary, QueueStats } from '@/utils/types';
import {
  Activity,
  Send,
  Clock,
  AlertTriangle,
  Server,
} from 'lucide-react';

export function DashboardHealth() {
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    adminApi.getHealthSummary().then(setHealth).catch(() => setError(true));
    adminApi.getWorkflowStats().then(setStats).catch(() => {});
  }, []);

  // Auto-start welcome tour on first visit
  useEffect(() => {
    if (shouldShowTour('welcome')) {
      const timer = setTimeout(() => startTour('welcome'), 800);
      return () => clearTimeout(timer);
    }
  }, []);

  return (
    <>
      {/* System Health Widget */}
      <div data-tour="dashboard-health">
        <Card>
          <div className="p-md">
            <div className="flex items-center gap-2 mb-3">
              <Activity className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-semibold m-0">System Health</h3>
            </div>

            {error ? (
              <div className="flex items-center gap-2 text-xs text-text-muted">
                <AlertTriangle className="w-3.5 h-3.5 text-error" />
                <span>Unable to fetch health data — API unavailable</span>
              </div>
            ) : !health ? (
              <p className="text-xs text-text-muted">Loading health data...</p>
            ) : (
              <div className="space-y-3">
                {/* Providers */}
                <div>
                  <div className="text-xs text-text-muted uppercase tracking-wide mb-1.5">
                    Providers
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(health.providers ?? []).map((p) => (
                      <div key={p.name} className="flex items-center gap-1.5 text-xs">
                        <span
                          className={`w-2 h-2 rounded-full ${
                            p.configured ? 'bg-success' : 'bg-text-muted'
                          }`}
                        />
                        {p.name}
                      </div>
                    ))}
                    {(health.providers ?? []).length === 0 && (
                      <span className="text-xs text-text-muted">No providers configured</span>
                    )}
                  </div>
                </div>

                {/* Databases */}
                <div>
                  <div className="text-xs text-text-muted uppercase tracking-wide mb-1.5">
                    Databases
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(health.databases ?? []).map((db) => (
                      <div key={db.name} className="flex items-center gap-1.5 text-xs">
                        <span
                          className={`w-2 h-2 rounded-full ${
                            db.status === 'healthy' ? 'bg-success' : 'bg-error'
                          }`}
                        />
                        {db.name}
                        {db.latency_ms != null && (
                          <span className="text-text-muted">({db.latency_ms}ms)</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Workers */}
                <div className="text-xs text-text-secondary">
                  {(health.workers?.active ?? 0)} active worker{(health.workers?.active ?? 0) !== 1 ? 's' : ''},{' '}
                  {(health.workers?.queued ?? 0)} queued run{(health.workers?.queued ?? 0) !== 1 ? 's' : ''}
                </div>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* Quick Actions */}
      <div data-tour="dashboard-actions" className="grid grid-cols-2 md:grid-cols-4 gap-md">
        <Link
          href="/workflows/dispatch"
          className="bg-surface-dark rounded-lg border border-border-dark p-md no-underline text-inherit hover:border-primary/40 hover:bg-surface-dark-hover transition-colors"
        >
          <Send className="w-4 h-4 text-primary mb-1.5" />
          <div className="text-sm font-semibold">Dispatch Workflow</div>
          <div className="text-xs text-text-muted mt-0.5">Enqueue a workflow run</div>
        </Link>

        <Link
          href="/workflows/approvals"
          className="bg-surface-dark rounded-lg border border-border-dark p-md no-underline text-inherit hover:border-primary/40 hover:bg-surface-dark-hover transition-colors"
        >
          <Clock className="w-4 h-4 text-amber-500 mb-1.5" />
          <div className="text-sm font-semibold flex items-center gap-1.5">
            Pending Approvals
            {stats && stats.waiting > 0 && (
              <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-500/15 text-amber-500">
                {stats.waiting}
              </span>
            )}
          </div>
          <div className="text-xs text-text-muted mt-0.5">Review waiting workflows</div>
        </Link>

        <Link
          href="/workflows/dlq"
          className="bg-surface-dark rounded-lg border border-border-dark p-md no-underline text-inherit hover:border-primary/40 hover:bg-surface-dark-hover transition-colors"
        >
          <AlertTriangle className="w-4 h-4 text-error mb-1.5" />
          <div className="text-sm font-semibold flex items-center gap-1.5">
            Failed Workflows
            {stats && stats.failed > 0 && (
              <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-error/15 text-error">
                {stats.failed}
              </span>
            )}
          </div>
          <div className="text-xs text-text-muted mt-0.5">Failed workflows to triage</div>
        </Link>

        <Link
          href="/workflows/workers"
          className="bg-surface-dark rounded-lg border border-border-dark p-md no-underline text-inherit hover:border-primary/40 hover:bg-surface-dark-hover transition-colors"
        >
          <Server className="w-4 h-4 text-primary mb-1.5" />
          <div className="text-sm font-semibold">Worker Health</div>
          <div className="text-xs text-text-muted mt-0.5">Monitor active workers</div>
        </Link>
      </div>
    </>
  );
}
