'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { adminApi } from '@/utils/api';
import type { RunSummary } from '@/utils/types';
import Link from 'next/link';
import { Badge, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { ResponsiveTable } from '@/components/responsive-table';

const RUN_TYPE_LABELS: Record<string, { label: string; variant: 'default' | 'info' | 'warning' }> = {
  standalone: { label: 'Standalone', variant: 'default' },
  workflow_step: { label: 'Workflow Step', variant: 'info' },
  directive_delegation: { label: 'Directive', variant: 'warning' },
};

export function RunsView() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [agentFilter, setAgentFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [showWorkflowSteps, setShowWorkflowSteps] = useState(false);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const fetchRuns = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    try {
      const page = await Promise.race([
        adminApi.listRuns({
          agent_name: agentFilter || undefined,
          status: statusFilter || undefined,
          include_workflow_steps: showWorkflowSteps,
          limit: 50,
        }),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('timeout')), 8000),
        ),
      ]);
      if (controller.signal.aborted) return;
      setRuns(page.items);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      if (!controller.signal.aborted) setRuns([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [agentFilter, statusFilter, showWorkflowSteps]);

  useEffect(() => {
    fetchRuns();
    return () => { abortRef.current?.abort(); };
  }, [fetchRuns]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await adminApi.listRuns({
        agent_name: agentFilter || undefined,
        status: statusFilter || undefined,
        include_workflow_steps: showWorkflowSteps,
        cursor,
        limit: 50,
      });
      setRuns(prev => [...prev, ...page.items]);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div>
      <div className="flex flex-wrap gap-3 mb-md items-center">
        <input
          placeholder="Filter by agent name..."
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
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
          <option value="failed">Failed</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-text-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showWorkflowSteps}
            onChange={(e) => setShowWorkflowSteps(e.target.checked)}
            className="rounded border-border"
          />
          Show workflow steps
        </label>
      </div>

      {loading ? (
        <Skeleton lines={5} />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No Runs Found"
          description="No runs found. Execute an agent to see runs here."
        />
      ) : (
        <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
          <ResponsiveTable
            columns={[
              { key: 'run_id', label: 'Run ID' },
              { key: 'agent', label: 'Agent' },
              { key: 'type', label: 'Type' },
              { key: 'status', label: 'Status' },
              { key: 'tokens', label: 'Tokens' },
              { key: 'started', label: 'Started' },
            ]}
            rows={runs.map((run) => {
              const typeInfo = RUN_TYPE_LABELS[run.run_type] || RUN_TYPE_LABELS.standalone;
              return {
                run_id: (
                  <Link href={`/runs/${run.run_id}`} className="text-primary no-underline font-[family-name:var(--font-mono)] text-xs hover:underline">
                    {run.run_id.slice(0, 12)}...
                  </Link>
                ),
                agent: run.agent_name,
                type: (
                  <div className="flex items-center gap-1.5">
                    <Badge variant={typeInfo.variant}>{typeInfo.label}</Badge>
                    {run.parent_workflow_run_id && (
                      <Link
                        href={`/workflows/history/${run.parent_workflow_run_id}`}
                        className="text-primary text-xs no-underline hover:underline"
                        title="View parent workflow"
                      >
                        &rarr; workflow
                      </Link>
                    )}
                  </div>
                ),
                status: (
                  <Badge variant={run.status === 'completed' ? 'success' : run.status === 'running' ? 'info' : run.status === 'failed' ? 'error' : 'default'}>
                    {run.status}
                  </Badge>
                ),
                tokens: <span className="text-text-muted">{run.total_tokens.toLocaleString()}</span>,
                started: (
                  <span className="text-text-muted text-xs">
                    {run.started_at ? new Date(run.started_at * 1000).toLocaleString() : '—'}
                  </span>
                ),
              };
            })}
          />
        </div>
      )}

      {hasMore && (
        <div className="flex justify-center mt-md">
          <Button variant="secondary" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? 'Loading...' : 'Load more'}
          </Button>
        </div>
      )}
    </div>
  );
}
