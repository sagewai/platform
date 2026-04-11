'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { adminApi } from '@/utils/api';
import type { RunSummary, WorkflowRun } from '@/utils/types';
import Link from 'next/link';
import { Card, Badge, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { ResponsiveTable } from '@/components/responsive-table';

/** Unified run row — can come from agent_runs or workflow_runs */
interface UnifiedRun {
  run_id: string;
  name: string;
  source: 'agent' | 'workflow';
  status: string;
  total_tokens: number;
  started_at: number | null;
  detail_href: string;
}

function toUnifiedFromAgent(r: RunSummary): UnifiedRun {
  return {
    run_id: r.run_id,
    name: r.agent_name,
    source: 'agent',
    status: r.status,
    total_tokens: r.total_tokens,
    started_at: r.started_at,
    detail_href: `/runs/${r.run_id}`,
  };
}

function toUnifiedFromWorkflow(r: WorkflowRun): UnifiedRun {
  const created = new Date(r.created_at).getTime() / 1000;
  return {
    run_id: r.run_id,
    name: r.workflow_name,
    source: 'workflow',
    status: r.status,
    total_tokens: 0,
    started_at: created,
    detail_href: `/workflows/history/${r.run_id}`,
  };
}

export default function AgentRunsPage() {
  const [runs, setRuns] = useState<UnifiedRun[]>([]);
  const [nameFilter, setNameFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState<'' | 'agent' | 'workflow'>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Fetch both sources in parallel
      const [agentPage, workflowRuns] = await Promise.all([
        adminApi.listRuns({ limit: 100 }).catch(() => ({ items: [] as RunSummary[], next_cursor: null, has_more: false })),
        adminApi.listWorkflowRuns({ limit: 100 }).catch(() => [] as WorkflowRun[]),
      ]);

      const agentUnified = agentPage.items.map(toUnifiedFromAgent);
      const workflowUnified = workflowRuns.map(toUnifiedFromWorkflow);

      // Merge and sort by started_at descending
      const all = [...agentUnified, ...workflowUnified].sort(
        (a, b) => (b.started_at ?? 0) - (a.started_at ?? 0),
      );
      setRuns(all);
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

  // Client-side filtering
  const filtered = useMemo(() => {
    let result = runs;
    if (nameFilter) {
      const q = nameFilter.toLowerCase();
      result = result.filter((r) => r.name.toLowerCase().includes(q));
    }
    if (statusFilter) {
      result = result.filter((r) => r.status === statusFilter);
    }
    if (sourceFilter) {
      result = result.filter((r) => r.source === sourceFilter);
    }
    return result;
  }, [runs, nameFilter, statusFilter, sourceFilter]);

  const stats = useMemo(() => {
    const total = runs.length;
    const agentCount = runs.filter((r) => r.source === 'agent').length;
    const workflowCount = runs.filter((r) => r.source === 'workflow').length;
    const completed = runs.filter((r) => r.status === 'completed').length;
    const failed = runs.filter((r) => r.status === 'failed').length;
    const completedPct = total > 0 ? Math.round((completed / total) * 100) : 0;
    return { total, agentCount, workflowCount, completedPct, failed };
  }, [runs]);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-md">
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">
          All Runs
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Unified execution history — agent runs from Playground and workflow runs combined.
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
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Agent Runs</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {stats.agentCount}
              </p>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Workflow Runs</p>
              <p className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {stats.workflowCount}
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
          placeholder="Filter by name..."
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
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value as '' | 'agent' | 'workflow')}
          className="px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
        >
          <option value="">All sources</option>
          <option value="agent">Agent (Playground)</option>
          <option value="workflow">Workflow</option>
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
          title="No Runs Found"
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
              { key: 'name', label: 'Name' },
              { key: 'source', label: 'Source' },
              { key: 'status', label: 'Status' },
              { key: 'tokens', label: 'Tokens' },
              { key: 'started', label: 'Started' },
            ]}
            rows={filtered.map((run) => ({
              run_id: (
                <Link
                  href={run.detail_href}
                  className="text-primary no-underline font-[family-name:var(--font-mono)] text-xs hover:underline"
                >
                  {run.run_id.slice(0, 12)}...
                </Link>
              ),
              name: run.name,
              source: (
                <Badge variant={run.source === 'workflow' ? 'warning' : 'info'}>
                  {run.source}
                </Badge>
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
