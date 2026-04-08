'use client';

import { useEffect, useState, useCallback } from 'react';
import { Card, Badge, EmptyState } from '@sagecurator/ui';
import { adminApi } from '@/utils/api';
import type { WorkerInfo } from '@/utils/types';
import { HelpCircle, RefreshCw, Server } from 'lucide-react';
import { startTour } from '@/utils/tours';

export default function WorkersPage() {
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showHelp, setShowHelp] = useState(false);

  const fetchWorkers = useCallback(async () => {
    try {
      const data = await adminApi.listWorkers();
      setWorkers(data);
    } catch {
      setWorkers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchWorkers();
    const interval = setInterval(fetchWorkers, 5000);
    return () => clearInterval(interval);
  }, [fetchWorkers]);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">
            Workflow Workers
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Active workers processing workflow queue
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => startTour('workflow-ops')}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
            title="Take a tour"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            Tour
          </button>
          <button
            onClick={() => setShowHelp(!showHelp)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
            title="Help"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            Help
          </button>
          <button
            onClick={fetchWorkers}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {showHelp && (
        <Card>
          <div className="p-md text-sm text-text-secondary space-y-2">
            <p>Workers are background processes that execute queued workflows. Start one with <code className="text-[13px] bg-bg-subtle px-1 py-0.5 rounded">sagewai worker start</code>. Each worker polls the queue and can run multiple workflows concurrently. Monitor heartbeat timestamps to ensure workers are responsive.</p>
          </div>
        </Card>
      )}

      {loading ? (
        <Card><p className="text-text-muted p-md text-sm">Loading workers...</p></Card>
      ) : workers.length === 0 ? (
        <EmptyState
          icon={<Server className="w-10 h-10" />}
          title="No active workers"
          description="Start a worker with: sagewai worker start --concurrency 4"
        />
      ) : (
        <Card>
          <div data-tour="workers-table">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Worker ID</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Active Runs</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Last Heartbeat</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Status</th>
              </tr>
            </thead>
            <tbody>
              {workers.map((w) => {
                const heartbeatDate = new Date(w.last_heartbeat);
                const secondsAgo = Math.floor((Date.now() - heartbeatDate.getTime()) / 1000);
                const isHealthy = secondsAgo < 120;
                return (
                  <tr key={w.owner_id} className="border-b border-border last:border-0 hover:bg-bg-subtle">
                    <td className="py-2.5 px-3 font-[family-name:var(--font-mono)] text-[13px]">
                      {w.owner_id}
                    </td>
                    <td className="py-2.5 px-3">{w.active_runs}</td>
                    <td className="py-2.5 px-3 text-text-secondary">
                      {secondsAgo < 60 ? `${secondsAgo}s ago` : `${Math.floor(secondsAgo / 60)}m ago`}
                    </td>
                    <td className="py-2.5 px-3">
                      <Badge variant={isHealthy ? 'success' : 'error'}>
                        {isHealthy ? 'Healthy' : 'Stale'}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </Card>
      )}
    </div>
  );
}
