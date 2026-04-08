'use client';

import { adminApi } from '@/utils/api';
import type { WorkflowRun, WorkflowRunStatus } from '@/utils/types';
import Link from 'next/link';
import { useEffect, useState, useRef, useCallback } from 'react';
import { Card, Badge, Button, EmptyState, Skeleton } from '@sagecurator/ui';
import { Search, X, RotateCcw } from 'lucide-react';

function statusVariant(status: string): 'success' | 'error' | 'info' | 'warning' | 'default' {
  switch (status) {
    case 'completed': return 'success';
    case 'failed': return 'error';
    case 'running': return 'info';
    case 'pending': return 'warning';
    case 'cancelled': return 'default';
    default: return 'default';
  }
}

const PAGE_SIZE = 50;

export default function WorkflowHistoryPage() {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<WorkflowRunStatus | ''>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const refreshRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Debounce search input
  useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [searchQuery]);

  const abortRef = useRef<AbortController | null>(null);

  const fetchRuns = useCallback(async (showLoading = true) => {
    // Cancel any in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (showLoading) setLoading(true);
    try {
      const data = await Promise.race([
        adminApi.listWorkflowRuns({
          limit: PAGE_SIZE + 1,
          status: statusFilter || undefined,
          search: debouncedSearch || undefined,
          offset: 0,
        }),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('timeout')), 8000),
        ),
      ]);
      if (controller.signal.aborted) return;
      setHasMore(data.length > PAGE_SIZE);
      setRuns(data.slice(0, PAGE_SIZE));
      setOffset(0);
    } catch {
      if (!controller.signal.aborted) setRuns([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [statusFilter, debouncedSearch]);

  useEffect(() => {
    fetchRuns();
    return () => { abortRef.current?.abort(); };
  }, [fetchRuns]);

  // Auto-refresh every 5s when there are active runs (not 3s to reduce thrashing)
  useEffect(() => {
    const hasActive = runs.some((r) => r.status === 'running' || r.status === 'pending');
    if (hasActive) {
      refreshRef.current = setInterval(() => fetchRuns(false), 5000);
    }
    return () => {
      if (refreshRef.current) clearInterval(refreshRef.current);
    };
  }, [runs, fetchRuns]);

  async function loadMore() {
    const nextOffset = offset + PAGE_SIZE;
    setLoadingMore(true);
    try {
      const data = await adminApi.listWorkflowRuns({
        limit: PAGE_SIZE + 1,
        status: statusFilter || undefined,
        search: debouncedSearch || undefined,
        offset: nextOffset,
      });
      setHasMore(data.length > PAGE_SIZE);
      setRuns((prev) => [...prev, ...data.slice(0, PAGE_SIZE)]);
      setOffset(nextOffset);
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Workflow History</h1>
        <Skeleton lines={5} />
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-lg flex-wrap gap-3">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">Workflow History</h1>
        <div className="flex items-center gap-sm flex-wrap">
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name..."
              className="pl-8 pr-7 py-1.5 text-sm bg-bg-subtle border border-border rounded w-[200px] outline-none focus:border-primary text-text-primary"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-white border-none bg-transparent cursor-pointer"
              >
                <X size={12} />
              </button>
            )}
          </div>

          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as WorkflowRunStatus | '')}
            className="text-sm bg-bg-subtle border border-border rounded px-2 py-1.5 text-text-primary"
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>

          <button
            type="button"
            onClick={() => fetchRuns()}
            className="text-sm text-text-muted hover:text-primary border border-border rounded px-3 py-1.5 bg-transparent cursor-pointer"
          >
            Refresh
          </button>
        </div>
      </div>

      {runs.length === 0 ? (
        <EmptyState
          title="No Workflow Runs"
          description={
            statusFilter || debouncedSearch
              ? 'No workflow runs match your filters.'
              : 'No workflow runs yet. Run a workflow from the builder.'
          }
        />
      ) : (
        <Card>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Workflow</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Run ID</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Status</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Progress</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Created</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-2.5 px-3 font-medium">{r.workflow_name}</td>
                  <td className="py-2.5 px-3">
                    <Link
                      href={`/workflows/history/${r.run_id}`}
                      className="text-primary no-underline hover:underline text-[13px] font-[family-name:var(--font-mono)]"
                    >
                      {r.run_id.slice(0, 12)}...
                    </Link>
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge variant={statusVariant(r.status)}>{r.status}</Badge>
                  </td>
                  <td className="py-2.5 px-3">
                    {r.steps_total ? (
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-bg-subtle rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary rounded-full transition-all"
                            style={{ width: `${Math.round(((r.steps_completed ?? 0) / r.steps_total) * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-text-muted">
                          {r.steps_completed ?? 0}/{r.steps_total}
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-text-muted">&mdash;</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-[13px] text-text-muted">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex items-center gap-1">
                      <Link
                        href={`/workflows/history/${r.run_id}`}
                        className="text-[11px] text-primary no-underline hover:underline"
                      >
                        View
                      </Link>
                      {Boolean((r.input as Record<string, unknown> | undefined)?.yaml || r.data?.yaml) && (
                        <button
                          type="button"
                          onClick={() => {
                            const inp = r.input as Record<string, unknown> | undefined;
                            const yamlStr = encodeURIComponent(String(inp?.yaml ?? r.data?.yaml ?? ''));
                            const msg = inp?.message ? encodeURIComponent(String(inp.message)) : '';
                            window.location.href = `/workflows?yaml=${yamlStr}&message=${msg}`;
                          }}
                          className="flex items-center gap-0.5 text-[11px] text-text-muted hover:text-primary border-none bg-transparent cursor-pointer"
                          title="Re-run this workflow"
                        >
                          <RotateCcw size={10} />
                          Re-run
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* Load more */}
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
