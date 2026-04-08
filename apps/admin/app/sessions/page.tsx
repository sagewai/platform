'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';

import { adminApi } from '@/utils/api';
import type { SessionInfo } from '@/utils/types';
import { Badge, Button, Skeleton, EmptyState } from '@sagecurator/ui';
import { ResponsiveTable } from '@/components/responsive-table';

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const page = await adminApi.listSessions({ limit: 50 });
      setSessions(page.items);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      // API unavailable
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await adminApi.listSessions({ cursor, limit: 50 });
      setSessions(prev => [...prev, ...page.items]);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Live Sessions</h1>
      {loading ? (
        <Skeleton lines={5} />
      ) : sessions.length === 0 ? (
        <EmptyState
          title="No Active Sessions"
          description="No active sessions. Start a chat to see live sessions here."
        />
      ) : (
        <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
          <ResponsiveTable
            columns={[
              { key: 'session_id', label: 'Session ID' },
              { key: 'agent', label: 'Agent' },
              { key: 'status', label: 'Status' },
              { key: 'messages', label: 'Messages' },
              { key: 'started', label: 'Started' },
            ]}
            rows={sessions.map((s) => ({
              session_id: (
                <Link
                  href={`/sessions/${s.session_id}`}
                  className="text-primary no-underline hover:underline font-[family-name:var(--font-mono)] text-xs"
                >
                  {s.session_id.slice(0, 12)}...
                </Link>
              ),
              agent: s.agent_name,
              status: (
                <Badge variant={s.status === 'active' ? 'info' : 'default'}>
                  {s.status}
                </Badge>
              ),
              messages: <span className="text-text-muted">{s.message_count}</span>,
              started: (
                <span className="text-text-muted text-xs">
                  {new Date(s.started_at * 1000).toLocaleString()}
                </span>
              ),
            }))}
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
