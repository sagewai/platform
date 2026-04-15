'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { adminApi } from '@/utils/api';
import type { RunSummary } from '@/utils/types';
import Link from 'next/link';
import { Card, Badge, Skeleton, EmptyState } from '@/components/ui/legacy';
import { ResponsiveTable } from '@/components/responsive-table';

export default function AgentRunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [nameFilter, setNameFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [runTypeFilter, setRunTypeFilter] = useState<'' | 'standalone' | 'workflow_step'>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await adminApi.listRuns({ limit: 200 });
      setRuns(page.items);
    } catch (err: any) {
      setRuns([]);
      setError(err?.message || 'Failed to load runs. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  // Client-side filtering — all records are agent runs; run_type
  // distinguishes standalone playground invocations from inline steps
  // that ran inside a workflow.
  const filtered = useMemo(() => {
    let result = runs;
    if (nameFilter) {
      const q = nameFilter.toLowerCase();
      result = result.filter((r) => r.agent_name.toLowerCase().includes(q));
    }
    if (statusFilter) {
      result = result.filter((r) => r.status === statusFilter);
    }
    if (runTypeFilter) {
      result = result.filter((r) => r.run_type === runTypeFilter);
    }
    return result;
  }, [runs, nameFilter, statusFilter, runTypeFilter]);

  const stats = useMemo(() => {
    const total = runs.length;
    const standalone = runs.filter((r) => r.run_type === 'standalone').length;
    const inWorkflow = runs.filter((r) => r.run_type === 'workflow_step').length;
    const completed = runs.filter((r) => r.status === 'completed').length;
    const failed = runs.filter((r) => r.status === 'failed').length;
    const completedPct = total > 0 ? Math.round((completed / total) * 100) : 0;
    return { total, standalone, inWorkflow, completedPct, failed };
  }, [runs]);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-md">
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Agent Runs
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Every agent execution — standalone playground runs and inline agents invoked by a workflow.
          For group-level workflow metrics, see <Link href="/workflows/history" className="text-primary no-underline hover:underline">Workflow History</Link>.
        </p>
      </div>

      {/* Stats row */}
      {!loading && runs.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-md">
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Total</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {stats.total}
              </p>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Standalone</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {stats.standalone}
              </p>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">In Workflow</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {stats.inWorkflow}
              </p>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Completed %</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)] text-success">
                {stats.completedPct}%
              </p>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Failed</p>
              <p className={`text-2xl font-bold font-[family-name:var(--font-heading)] ${stats.failed > 0 ? 'text-error' : ''}`}>
                {stats.failed}
              </p>
            </div>
          </Card>
        </div>
      )}

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md flex items-center justify-between">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => fetchRuns()}
            className="text-xs text-error hover:text-white border border-error/30 rounded px-2 py-1 bg-transparent cursor-pointer ml-3"
          >
            Retry
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3 mb-md flex-wrap">
        <input
          placeholder="Filter by agent name..."
          value={nameFilter}
          onChange={(e) => setNameFilter(e.target.value)}
          className="px-3 py-2 border border-border rounded-md text-sm w-[220px] bg-bg-surface"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
        >
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="running">Running</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <select
          value={runTypeFilter}
          onChange={(e) => setRunTypeFilter(e.target.value as '' | 'standalone' | 'workflow_step')}
          className="px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
        >
          <option value="">All run types</option>
          <option value="standalone">Standalone (Playground)</option>
          <option value="workflow_step">In Workflow</option>
        </select>
        <button
          type="button"
          onClick={() => fetchRuns()}
          className="px-3 py-2 border border-border rounded-md text-sm bg-transparent text-text-muted hover:text-primary cursor-pointer"
        >
          Refresh
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <Skeleton lines={5} />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No Agent Runs"
          description={
            runs.length === 0
              ? 'No execution history yet. Run an agent in the Playground or execute a workflow to see runs here.'
              : 'No runs match your filters.'
          }
        />
      ) : (
        <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
          <ResponsiveTable
            columns={[
              { key: 'run_id', label: 'Run ID' },
              { key: 'agent_name', label: 'Agent' },
              { key: 'run_type', label: 'Run Type' },
              { key: 'status', label: 'Status' },
              { key: 'tokens', label: 'Tokens' },
              { key: 'started', label: 'Started' },
            ]}
            rows={filtered.map((run) => ({
              run_id: (
                <Link
                  href={`/runs/${run.run_id}`}
                  className="text-primary no-underline font-[family-name:var(--font-mono)] text-xs hover:underline"
                >
                  {run.run_id.slice(0, 12)}...
                </Link>
              ),
              agent_name: run.agent_name,
              run_type:
                run.run_type === 'workflow_step' && run.parent_workflow_run_id ? (
                  <Link
                    href={`/workflows/history/${run.parent_workflow_run_id}`}
                    className="no-underline"
                  >
                    <Badge variant="warning">in workflow</Badge>
                  </Link>
                ) : (
                  <Badge variant="info">standalone</Badge>
                ),
              status: (
                <Badge
                  variant={
                    run.status === 'completed'
                      ? 'success'
                      : run.status === 'running'
                        ? 'info'
                        : run.status === 'failed'
                          ? 'error'
                          : run.status === 'pending'
                            ? 'warning'
                            : 'default'
                  }
                >
                  {run.status}
                </Badge>
              ),
              tokens: (
                <span className="text-text-muted">
                  {run.total_tokens > 0 ? run.total_tokens.toLocaleString() : '\u2014'}
                </span>
              ),
              started: (
                <span className="text-text-muted text-xs">
                  {run.started_at
                    ? new Date(run.started_at * 1000).toLocaleString()
                    : '\u2014'}
                </span>
              ),
            }))}
          />
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <p className="text-xs text-text-muted mt-sm">
          Showing {filtered.length} of {runs.length} total runs
        </p>
      )}
    </div>
  );
}
