'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { AuditEvent } from '@/utils/types';
import { ExportButton } from '@/components/export-button';
import { Badge, Button, Card, Skeleton, EmptyState } from '@sagecurator/ui';

const PAGE_SIZE = 50;

const EVENT_TYPE_VARIANTS: Record<string, 'error' | 'warning' | 'info' | 'default'> = {
  pii_detected: 'error',
  hallucination: 'warning',
  content_filter: 'info',
};

export default function AuditLogPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // Filters
  const [agentFilter, setAgentFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    try {
      const page = await adminApi.listAuditEvents({
        agent_name: agentFilter || undefined,
        event_type: typeFilter || undefined,
        limit: PAGE_SIZE,
      });
      setEvents(page.items);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
      setError(null);
    } catch {
      setError('Failed to load audit events. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, [agentFilter, typeFilter]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  async function handleLoadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await adminApi.listAuditEvents({
        agent_name: agentFilter || undefined,
        event_type: typeFilter || undefined,
        cursor,
        limit: PAGE_SIZE,
      });
      setEvents(prev => [...prev, ...page.items]);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      setError('Failed to load more events.');
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">Audit Log</h1>
          <p className="mt-0 text-sm text-text-secondary">
            Browse guardrail events across all agents with filtering and export.
          </p>
        </div>
        <div className="flex gap-2">
          <ExportButton
            format="json"
            label="Export JSON"
            params={{
              ...(agentFilter ? { agent_name: agentFilter } : {}),
              ...(typeFilter ? { event_type: typeFilter } : {}),
            }}
          />
          <ExportButton
            format="csv"
            label="Export CSV"
            params={{
              ...(agentFilter ? { agent_name: agentFilter } : {}),
              ...(typeFilter ? { event_type: typeFilter } : {}),
            }}
          />
        </div>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Filters */}
      <Card className="mb-md">
        <div className="flex gap-3 items-center flex-wrap">
          <label className="text-[13px] text-text-muted">
            Agent:
            <input
              type="text"
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              placeholder="All agents"
              className="ml-1.5 px-2.5 py-1.5 border border-border rounded text-[13px] w-[150px] bg-bg-surface"
            />
          </label>
          <label className="text-[13px] text-text-muted">
            Event type:
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="ml-1.5 px-2.5 py-1.5 border border-border rounded text-[13px] bg-bg-surface"
            >
              <option value="">All types</option>
              <option value="pii_detected">PII Detected</option>
              <option value="hallucination">Hallucination</option>
              <option value="content_filter">Content Filter</option>
            </select>
          </label>
          <span className="text-[13px] text-text-muted">
            {events.length} event{events.length !== 1 ? 's' : ''} loaded
          </span>
        </div>
      </Card>

      {/* Events table */}
      <Card className="mb-md">
        {loading ? (
          <Skeleton lines={5} />
        ) : events.length === 0 ? (
          <EmptyState
            title="No Audit Events"
            description="No audit events found. Events will appear as guardrails trigger on agent output."
          />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">ID</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Agent</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Event Type</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Detail</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Time</th>
              </tr>
            </thead>
            <tbody>
              {events.map((evt) => (
                <tr key={evt.id} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-2.5 px-3 text-[13px] text-text-muted">#{evt.id}</td>
                  <td className="py-2.5 px-3 font-medium">{evt.agent_name}</td>
                  <td className="py-2.5 px-3">
                    <Badge variant={EVENT_TYPE_VARIANTS[evt.event_type] ?? 'default'}>
                      {evt.event_type}
                    </Badge>
                  </td>
                  <td
                    className="py-2.5 px-3 text-[13px] text-text-secondary max-w-[300px] overflow-hidden text-ellipsis whitespace-nowrap"
                    title={evt.detail ?? ''}
                  >
                    {evt.detail ?? '--'}
                  </td>
                  <td className="py-2.5 px-3 text-xs text-text-muted">
                    {evt.created_at
                      ? new Date(evt.created_at).toLocaleString()
                      : '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {hasMore && (
        <div className="flex justify-center">
          <Button variant="secondary" onClick={handleLoadMore} disabled={loadingMore}>
            {loadingMore ? 'Loading...' : 'Load more'}
          </Button>
        </div>
      )}
    </div>
  );
}
